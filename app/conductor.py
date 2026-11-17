"""Conductor (brief section 4, "Conductor rules").

Works from the fixed question bank. Its only freedoms:
  - reword a question to fit the idea
  - skip a question an earlier answer already covers
  - ask one follow-up when an answer is vague

Two calls:
  plan()   - once, when the idea is described: classifies the business type and
             rewords every remaining question.
  review() - after each free-text answer: decides on a follow-up and on skips.

The AI runs one of two ways:
  - through the Claude API, when ANTHROPIC_API_KEY is set in .env
  - otherwise through the Claude Code command-line tool, which uses the Claude
    subscription the owner is logged in with (no key, local use only)

If neither is available, or a call fails or times out, both calls return None
and the form carries on with the fixed questions.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import List, Optional

import anthropic
from pydantic import BaseModel
from typing_extensions import Literal

from .questions import QUESTIONS, QUESTIONS_BY_ID

log = logging.getLogger("conductor")

MODEL = os.environ.get("CONDUCTOR_MODEL") or "claude-opus-5-5"
CLI_MODEL = os.environ.get("CONDUCTOR_CLI_MODEL") or "claude-sonnet-5-5"
IDEA_QUESTION = "q8"

# Server-side refusal fallback is only offered on these model families.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

_client: Optional[anthropic.Anthropic] = None


def _cli_path() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/claude")
    return fallback if os.access(fallback, os.X_OK) else None


def backend() -> Optional[str]:
    """"api", "cli", or None when no AI is available."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if _cli_path():
        return "cli"
    return None


def enabled() -> bool:
    return backend() is not None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ---------- output shapes ----------

class Reworded(BaseModel):
    id: str
    text: str
    hint: Optional[str] = None
    options: Optional[List[str]] = None


class Skip(BaseModel):
    id: str
    answer: str


class Plan(BaseModel):
    business_type: Literal["product", "service", "software", "local_business", "marketplace"]
    follow_up: Optional[str] = None
    questions: List[Reworded]
    skips: List[Skip]


class Review(BaseModel):
    follow_up: Optional[str] = None
    skips: List[Skip]


# ---------- prompts ----------

COMMON = """You are the Conductor of a business-idea checker. A person is filling in a \
form that asks one question per screen about a business idea they want to test. The \
questions come from a fixed bank: you never add a question, drop one, or change what a \
question is asking for. The person may be a first-time founder anywhere in the world, so \
write the way a sharp, friendly advisor would speak: plain words, no jargon, no flattery, \
and never suggest an answer or assume a fact they have not given.

Skips: list a remaining question only when what the person has already said answers it \
clearly and completely, and give that answer in their own terms so it can be stored in \
its place. A partial or implied answer is not enough; when unsure, leave the question in. \
Never skip a question marked no_skip.

Follow-up: the form allows at most one follow-up per question, so use it only when an \
answer is too vague to check against real market data, for example "everyone", "it's \
better", "not much". A short answer that names something specific is fine. When a \
follow-up is needed, write one short question asking for the single missing specific. \
Most answers need none; return null then."""

PLAN_SYSTEM = COMMON + """

The person has just described their idea. Do three things.

1. Classify the business type.
2. Reword every remaining question so it reads as if written for this idea: same intent, \
one sentence where possible, naming the actual product, customer or place when that makes \
it clearer. Hints are optional and one short line. For a question marked adapt_options, \
also return 4 to 7 short option labels fitted to the person's country and business type, \
for example the marketplaces and channels people really use there. Return options for no \
other question.
3. Decide on a follow-up to the idea description, and on skips, by the rules above."""

REVIEW_SYSTEM = COMMON + """

The person has just answered one question. Decide on a follow-up to that answer, and on \
skips among the remaining questions, by the rules above."""


# ---------- helpers ----------

def _answer_text(a: dict) -> str:
    if a.get("dont_know"):
        return "I don't know"
    value = a.get("value")
    parts = list(value) if isinstance(value, list) else [value]
    if a.get("other"):
        parts.append(a["other"])
    text = ", ".join(p for p in parts if p)
    fu = a.get("follow_up")
    if fu and fu.get("answer"):
        text += "\n   Follow-up: %s\n   Answer: %s" % (fu["question"], fu["answer"])
    return text


def _said_so_far(session: dict) -> str:
    lines = []
    for q in QUESTIONS:
        a = session["answers"].get(q["id"])
        if a and not a.get("inferred"):
            lines.append("- %s\n   %s" % (a["question"], _answer_text(a)))
    return "\n".join(lines)


def remaining(session: dict, after: str) -> List[dict]:
    """Questions after `after` that the person has not answered themselves."""
    ids = [q["id"] for q in QUESTIONS]
    out = []
    for q in QUESTIONS[ids.index(after) + 1:]:
        a = session["answers"].get(q["id"])
        if a is None or a.get("inferred"):
            out.append(q)
    return out


def _describe(questions: List[dict], session: dict) -> str:
    lines = []
    for q in questions:
        text = session.get("questions", {}).get(q["id"], {}).get("text", q["text"])
        flags = [f for f in ("adapt_options", "no_skip") if q.get(f)]
        line = "- %s (%s%s): %s" % (q["id"], q["type"], ", " + ", ".join(flags) if flags else "", text)
        if q.get("options"):
            line += "\n   options: " + " | ".join(o["label"] for o in q["options"])
        lines.append(line)
    return "\n".join(lines)


def _record(session: dict, purpose: str, model: str, tokens_in: int, tokens_out: int, started: float) -> None:
    usage = session.setdefault("usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0, "log": []})
    usage["calls"] += 1
    usage["input_tokens"] += tokens_in
    usage["output_tokens"] += tokens_out
    usage["log"].append({
        "purpose": purpose,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "seconds": round(time.time() - started, 1),
    })


def _call(session: dict, purpose: str, system: str, user: str, schema, max_tokens: int, timeout: float):
    if backend() == "api":
        return _call_api(session, purpose, system, user, schema, max_tokens, timeout)
    return _call_cli(session, purpose, system, user, schema, timeout)


def _call_api(session: dict, purpose: str, system: str, user: str, schema, max_tokens: int, timeout: float):
    kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        output_config={"effort": "low"},
    )
    if MODEL.startswith(_FALLBACK_PREFIXES):
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    started = time.time()
    try:
        response = client().with_options(timeout=timeout, max_retries=0).beta.messages.parse(**kwargs)
    except anthropic.AuthenticationError:
        log.error("%s: the API key was rejected. Check ANTHROPIC_API_KEY in .env", purpose)
        return None
    except anthropic.APITimeoutError:
        log.warning("%s: timed out after %.0fs, continuing without it", purpose, timeout)
        return None
    except anthropic.APIStatusError as e:
        log.error("%s: API error %s: %s", purpose, e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.error("%s: could not reach the API", purpose)
        return None

    _record(session, purpose, response.model, response.usage.input_tokens, response.usage.output_tokens, started)
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("%s: no usable result (stop_reason=%s)", purpose, response.stop_reason)
        return None
    return response.parsed_output


# The command-line tool's own --json-schema option hangs when it is not logged
# in, so the shape is requested in the prompt and checked here instead.
_JSON_ONLY = "\n\nReply with one JSON object and nothing else. It must match this JSON Schema:\n"


def _call_cli(session: dict, purpose: str, system: str, user: str, schema, timeout: float):
    """One call through the Claude Code command-line tool (uses the Claude subscription)."""
    cmd = [
        _cli_path(), "-p", user,
        "--output-format", "json",
        "--model", CLI_MODEL,
        "--effort", "low",
        "--system-prompt", system + _JSON_ONLY + json.dumps(schema.model_json_schema()),
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--tools", "",
    ]
    # A clean environment: no project settings, and nothing inherited from a
    # Claude session that may have started this server.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    started = time.time()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=env, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        log.warning("%s: timed out after %.0fs, continuing without it", purpose, timeout)
        return None
    try:
        data = json.loads(done.stdout)
    except ValueError:
        log.error("%s: unreadable reply from the claude command: %s", purpose, (done.stdout or done.stderr)[:300])
        return None
    if data.get("is_error"):
        message = str(data.get("result"))
        if "Not logged in" in message:
            message = "the claude command is not logged in. Run `claude` in Terminal and type /login"
        log.error("%s: %s", purpose, message)
        return None

    usage = data.get("usage") or {}
    tokens_in = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    _record(session, purpose, "+".join(data.get("modelUsage") or {}) or CLI_MODEL,
            tokens_in, usage.get("output_tokens") or 0, started)

    text = data.get("result") or ""
    try:
        # Tolerate a code fence or stray words around the JSON object.
        return schema.model_validate(json.loads(text[text.index("{"):text.rindex("}") + 1]))
    except ValueError as e:
        log.warning("%s: reply did not match the expected shape: %s", purpose, str(e)[:300])
        return None


def _apply_skips(session: dict, skips: List[Skip], allowed: List[dict]) -> None:
    allowed_ids = {q["id"] for q in allowed if not q.get("no_skip")}
    for s in skips:
        if s.id in allowed_ids and s.answer.strip():
            q = QUESTIONS_BY_ID[s.id]
            session["answers"][s.id] = {
                "question": session.get("questions", {}).get(s.id, {}).get("text", q["text"]),
                "value": s.answer.strip(),
                "other": None,
                "dont_know": False,
                "inferred": True,
            }


# ---------- the two calls ----------

def plan(session: dict, allow_follow_up: bool) -> Optional[str]:
    """Run when the idea is described. Updates the session; returns a follow-up or None."""
    todo = remaining(session, IDEA_QUESTION)
    if not todo:
        return None
    user = "What the person has said so far:\n%s\n\nRemaining questions:\n%s" % (
        _said_so_far(session), _describe(todo, session))
    if not allow_follow_up:
        user += "\n\nThe follow-up for the idea has already been used. Return null for follow_up."
    result = _call(session, "plan", PLAN_SYSTEM, user, Plan, max_tokens=8000, timeout=60.0)
    if result is None:
        return None

    session["business_type"] = result.business_type
    todo_by_id = {q["id"]: q for q in todo}
    overrides = session.setdefault("questions", {})
    for r in result.questions:
        q = todo_by_id.get(r.id)
        if q is None or not r.text.strip():
            continue
        entry = {"text": r.text.strip(), "hint": (r.hint or "").strip() or None}
        if q.get("adapt_options") and r.options:
            labels = [o.strip() for o in r.options if o.strip()][:7]
            if len(labels) >= 2:
                entry["options"] = [{"label": label} for label in labels]
        overrides[r.id] = entry
    _apply_skips(session, result.skips, todo)
    return (result.follow_up or "").strip() or None if allow_follow_up else None


def review(session: dict, question_id: str) -> Optional[str]:
    """Run after a free-text answer. Updates the session; returns a follow-up or None."""
    todo = remaining(session, question_id)
    answered = session["answers"][question_id]
    user = "What the person has said so far:\n%s\n\nThe answer to review:\n- %s\n   %s\n\nRemaining questions:\n%s" % (
        _said_so_far(session), answered["question"], _answer_text(answered),
        _describe(todo, session) or "(none)")
    result = _call(session, "review:" + question_id, REVIEW_SYSTEM, user, Review, max_tokens=4000, timeout=30.0)
    if result is None:
        return None
    _apply_skips(session, result.skips, todo)
    return (result.follow_up or "").strip() or None
