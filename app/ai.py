"""One way to ask the AI something, shared by the Conductor and the Researcher.

The AI runs one of two ways:
  - through the Claude API, when ANTHROPIC_API_KEY is set in .env
  - otherwise through the Claude Code command-line tool, which uses the Claude
    subscription the owner is logged in with (no key, local use only)

call() returns a validated pydantic object, or None when the AI is unavailable,
fails, times out or replies in the wrong shape. Callers always have a fallback.
Every call is logged in session["usage"] (tokens, searches, seconds).
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import anthropic

log = logging.getLogger("ai")

API_MODEL = os.environ.get("CONDUCTOR_MODEL") or "claude-opus-5-5"
CLI_MODEL = os.environ.get("CONDUCTOR_CLI_MODEL") or "claude-sonnet-5-5"

# Server-side refusal fallback is only offered on these model families.
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

# The shape is requested in the prompt and checked here, for both backends:
# the command-line tool's own --json-schema option hung when not logged in,
# and web search results are easier to handle as plain text replies.
_JSON_ONLY = "\n\nReply with one JSON object and nothing else. It must match this JSON Schema:\n"

_client: Optional[anthropic.Anthropic] = None


def cli_path() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/claude")
    return fallback if os.access(fallback, os.X_OK) else None


def backend() -> Optional[str]:
    """"api", "cli", or None when no AI is available."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if cli_path():
        return "cli"
    return None


def enabled() -> bool:
    return backend() is not None


def _api_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def record(session: dict, purpose: str, model: str, tokens_in: int, tokens_out: int,
           searches: int, started: float) -> None:
    usage = session.setdefault("usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0, "log": []})
    usage["calls"] += 1
    usage["input_tokens"] += tokens_in
    usage["output_tokens"] += tokens_out
    usage["searches"] = usage.get("searches", 0) + searches
    usage["log"].append({
        "purpose": purpose,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "searches": searches,
        "seconds": round(time.time() - started, 1),
    })


def _parse(purpose: str, text: str, schema):
    try:
        # Tolerate a code fence or stray words around the JSON object.
        return schema.model_validate(json.loads(text[text.index("{"):text.rindex("}") + 1]))
    except ValueError as e:
        log.warning("%s: reply did not match the expected shape: %s", purpose, str(e)[:300])
        return None


def call(session: dict, purpose: str, system: str, user: str, schema, *, max_tokens: int,
         timeout: float, effort: str = "low", web: bool = False):
    system = system + _JSON_ONLY + json.dumps(schema.model_json_schema())
    if backend() == "api":
        return _call_api(session, purpose, system, user, schema, max_tokens, timeout, effort, web)
    if backend() == "cli":
        return _call_cli(session, purpose, system, user, schema, timeout, effort, web)
    return None


def _call_api(session, purpose, system, user, schema, max_tokens, timeout, effort, web):
    kwargs = dict(
        model=API_MODEL,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": effort},
    )
    if web:
        kwargs["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 10},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
        ]
    if API_MODEL.startswith(_FALLBACK_PREFIXES):
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"

    messages = [{"role": "user", "content": user}]
    started = time.time()
    tokens_in = tokens_out = searches = 0
    api = _api_client().with_options(timeout=timeout, max_retries=0)
    try:
        # Web search turns can pause; continue them a few times.
        for _ in range(4):
            response = api.beta.messages.create(messages=messages, **kwargs)
            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens
            server = getattr(response.usage, "server_tool_use", None)
            searches += (getattr(server, "web_search_requests", 0) or 0) if server else 0
            if response.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": response.content}]
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

    record(session, purpose, response.model, tokens_in, tokens_out, searches, started)
    if response.stop_reason != "end_turn":
        log.warning("%s: no usable result (stop_reason=%s)", purpose, response.stop_reason)
        return None
    text = "".join(b.text for b in response.content if b.type == "text")
    return _parse(purpose, text, schema)


def _call_cli(session, purpose, system, user, schema, timeout, effort, web):
    """One call through the Claude Code command-line tool (uses the Claude subscription)."""
    tools = "WebSearch,WebFetch" if web else ""
    cmd = [
        cli_path(), "-p", user,
        "--output-format", "json",
        "--model", CLI_MODEL,
        "--effort", effort,
        "--system-prompt", system,
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--tools", tools,
    ]
    if web:
        cmd += ["--allowedTools", tools]
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
            message = "the claude command is not logged in. Run ~/.local/bin/claude in Terminal and type /login"
        log.error("%s: %s", purpose, message)
        return None

    usage = data.get("usage") or {}
    tokens_in = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests") or 0
    record(session, purpose, "+".join(data.get("modelUsage") or {}) or CLI_MODEL,
           tokens_in, usage.get("output_tokens") or 0, searches, started)
    return _parse(purpose, data.get("result") or "", schema)
