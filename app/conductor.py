"""Conductor (brief section 4, "Conductor rules").

Works from the fixed question bank. Its only freedoms:
  - reword a question to fit the idea
  - skip a question an earlier answer already covers
  - ask one follow-up when an answer is vague

Two calls:
  plan()   - once, when the idea is described: classifies the business type and
             rewords every remaining question.
  review() - after each free-text answer: decides on a follow-up and on skips.

If there is no API key, or a call fails or times out, both return None and the
form carries on with the fixed questions.
"""

import logging
import os
import time
from typing import List, Optional

import anthropic
from pydantic import BaseModel
from typing_extensions import Literal

from .questions import QUESTIONS, QUESTIONS_BY_ID

log = logging.getLogger("conductor")

MODEL = os.environ.get("CONDUCTOR_MODEL") or "claude-opus-5-5"
IDEA_QUESTION = "q8"

# Server-side refusal fallback is only offered on these model families.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

_client: Optional[anthropic.Anthropic] = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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


def _call(session: dict, purpose: str, system: str, user: str, schema, max_tokens: int, timeout: float):
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

    usage = session.setdefault("usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0, "log": []})
    usage["calls"] += 1
    usage["input_tokens"] += response.usage.input_tokens
    usage["output_tokens"] += response.usage.output_tokens
    usage["log"].append({
        "purpose": purpose,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "seconds": round(time.time() - started, 1),
    })

    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("%s: no usable result (stop_reason=%s)", purpose, response.stop_reason)
        return None
    return response.parsed_output


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
    result = _call(session, "plan", PLAN_SYSTEM, user, Plan, max_tokens=8000, timeout=40.0)
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
    result = _call(session, "review:" + question_id, REVIEW_SYSTEM, user, Review, max_tokens=4000, timeout=10.0)
    if result is None:
        return None
    _apply_skips(session, result.skips, todo)
    return (result.follow_up or "").strip() or None
