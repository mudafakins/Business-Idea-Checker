"""Checker (brief section 5 and 8): compares answers with research findings.

It raises two kinds of alert:
  amber - a concern worth adjusting
  red   - a serious problem: a broken non-negotiable, every sale losing money,
          or a legal or safety issue that could end the business

Rules enforced here in code, whatever the AI returns:
  - Every alert cites at least one fact from the fact store by its id, except
    where the person's own answers contradict each other: a broken
    non-negotiable, or a cost per sale at or above their own price.
  - Red is only allowed for the three serious categories; anything else is amber.
  - An alert keeps its key across runs, so it is not shown as new each time, and
    remembers whether the person has opened it.

Alerts never block the form; the Checker runs in the background.
"""

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from . import ai
from .questions import QUESTIONS, QUESTIONS_BY_ID

RED_CATEGORIES = {"non_negotiable", "loses_money", "legal"}
ANSWER_ONLY = {"non_negotiable", "loses_money"}   # may rest on the person's own answers
MAX_ALERTS = 8


class Alert(BaseModel):
    key: str
    severity: Literal["amber", "red"]
    category: Literal["price", "costs", "loses_money", "demand", "market", "competition",
                      "customer", "channel", "fit", "non_negotiable", "legal"]
    question_id: str
    you_said: str
    data_says: str
    fact_ids: List[str]
    suggestion: str


class Check(BaseModel):
    alerts: List[Alert]


SYSTEM = """You are the Checker in a business-idea checker. You compare what the person \
has answered so far with the research findings and with their own non-negotiables, and \
you raise an alert only where they conflict. You never block or lecture; the person keeps \
going and sees your alerts in a corner of the screen.

Raise an alert when:
- an answer is contradicted by the findings, for example a price far outside what \
competitors charge, a cost below what the data says it costs, a "why customers would \
switch" that every competitor already claims, a customer group the data says is small, \
shrinking, or stops buying after a short time;
- the numbers do not work: if the person's cost of one sale (including anything they \
added in a follow-up) is at or above their price, always raise it as its own red \
loses_money alert, separate from any alert about the price compared with competitors;
- the idea, as described, breaks one of the person's own non-negotiables or their \
budget, time or start-earning limit;
- a rule, licence or claims law in the findings could stop the business.

Do not raise an alert for an answer that is simply missing, vague or "I don't know", for \
general advice, or for anything the findings do not support. Fewer, sharper alerts are \
better. Every number you mention must come from the answers or the findings.

severity: "red" only for a broken non-negotiable (category non_negotiable), every sale \
losing money after costs (loses_money), or a legal or safety issue that could end the \
business (legal). Everything else is "amber".

For each alert:
- key: a short stable slug for the issue, such as price-above-market. If an existing \
alert covers the same issue, reuse its key.
- question_id: the question the alert is about (from the list below).
- you_said: what the person said, briefly, in their words.
- data_says: what the findings say, with the numbers, in one or two plain sentences.
- fact_ids: the ids of the findings you relied on, such as ["C3", "M2"]. When the alert \
rests only on the person's own answers (a broken non-negotiable, or their cost against \
their price), use [].
- suggestion: the smallest concrete change to the idea or the answer that would fix it.

You are given the alerts already shown. Return the full current list: keep the ones that \
are still true (same key, wording may improve), drop the ones the person's answers now \
fix, and add new ones. Return an empty list if nothing conflicts."""


def _answer_text(a: dict) -> str:
    if a.get("dont_know"):
        return "I don't know"
    value = a.get("value")
    parts = list(value) if isinstance(value, list) else [value]
    if a.get("other"):
        parts.append(a["other"])
    text = ", ".join(str(p) for p in parts if p)
    fu = a.get("follow_up") or {}
    if fu.get("answer"):
        text += " (asked: %s / answered: %s)" % (fu["question"], fu["answer"])
    return text


def _prompt(session: dict) -> str:
    lines = ["ANSWERS SO FAR"]
    for q in QUESTIONS:
        a = session["answers"].get(q["id"])
        if a:
            note = " [taken from earlier answers]" if a.get("inferred") else ""
            lines.append("- %s: %s\n   %s%s" % (q["id"], a.get("question") or q["text"], _answer_text(a), note))
    lines.append("\nFINDINGS")
    facts = session.get("facts") or []
    for f in facts:
        source = f.get("source_name") or "no source"
        reason = " (estimate: %s)" % f["reasoning"] if f.get("tag") == "estimated" and f.get("reasoning") else ""
        lines.append("- %s [%s, %s, %s]: %s%s" % (f["id"], f["topic"], f["tag"], source, f["claim"], reason))
    if not facts:
        lines.append("(no research findings yet)")
    for key in ("research", "market_research"):
        note = (session.get(key) or {}).get("market_note")
        if note:
            lines.append("Research note: " + note)
    lines.append("\nALERTS ALREADY SHOWN")
    current = [a for a in session.get("alerts") or [] if a.get("status") == "open"]
    for a in current:
        lines.append("- %s (%s, about %s): %s" % (a["key"], a["severity"], a["question_id"], a["data_says"]))
    if not current:
        lines.append("(none)")
    return "\n".join(lines)


def run(session: dict) -> Optional[List[dict]]:
    """Check the answers against the findings. Returns cleaned alerts, or None if the check failed."""
    result = ai.call(session, "check", SYSTEM, _prompt(session), Check,
                     max_tokens=8000, timeout=120.0, effort="medium")
    if result is None:
        return None
    return _clean(result.alerts, session)


def _clean(alerts: List[Alert], session: dict) -> List[dict]:
    facts = {f["id"]: f for f in session.get("facts") or []}
    out, seen = [], set()
    for a in alerts:
        key = re.sub(r"[^a-z0-9]+", "-", a.key.lower()).strip("-")[:60]
        if not key or key in seen or a.question_id not in session["answers"]:
            continue
        cited = [fid for fid in a.fact_ids if fid in facts]
        if not cited and a.category not in ANSWER_ONLY:
            continue           # an alert must rest on evidence
        seen.add(key)
        severity = a.severity if a.category in RED_CATEGORIES else "amber"
        out.append({
            "key": key,
            "severity": severity,
            "category": a.category,
            "question_id": a.question_id,
            "question": session["answers"][a.question_id].get("question")
                        or QUESTIONS_BY_ID.get(a.question_id, {}).get("text"),
            "you_said": a.you_said.strip(),
            "data_says": a.data_says.strip(),
            "suggestion": a.suggestion.strip(),
            "sources": [{"id": fid, "claim": facts[fid]["claim"], "tag": facts[fid]["tag"],
                         "name": facts[fid].get("source_name"), "url": facts[fid].get("source_url")}
                        for fid in cited],
        })
    out.sort(key=lambda x: x["severity"] != "red")
    return out[:MAX_ALERTS]


def merge(existing: List[dict], fresh: List[dict]) -> List[dict]:
    """Keep alert history stable: same key keeps its first-raised time and seen flag;
    alerts no longer raised are kept as resolved, so the report can still list them."""
    now = datetime.now().isoformat(timespec="seconds")
    before = {a["key"]: a for a in existing}
    merged = []
    for a in fresh:
        old = before.pop(a["key"], None)
        a["status"] = "open"
        a["raised_at"] = old["raised_at"] if old else now
        a["seen"] = bool(old and old.get("seen") and old.get("severity") == a["severity"])
        merged.append(a)
    for old in before.values():
        if old.get("status") == "open":
            old = dict(old, status="resolved", resolved_at=now)
        merged.append(old)
    return merged
