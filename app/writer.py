"""Writer (brief sections 7 and 8): turns the scores and facts into plain language.

The verdict, the six ratings and every money figure come from scorer.py and are
shown by the page with their tags. The Writer only explains them and writes the
plan and next steps; it is told the verdict and may not change it.
"""

import json
from typing import List, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from . import ai, knowledge
from .checker import _answer_text
from .questions import QUESTIONS

VERDICT_NAMES = {"pursue": "Pursue", "test": "Test small first", "drop": "Drop or rethink"}


class AreaLine(BaseModel):
    area: Literal["demand", "market", "competition", "money", "fit", "risk"]
    line: str


class Gate(BaseModel):
    measure: str
    target: str


class CheapestTest(BaseModel):
    what: str
    budget: str
    time_limit: str


class Report(BaseModel):
    verdict_reasons: str
    area_lines: List[AreaLine]
    idea_sentence: str
    adjusted_idea: Optional[str] = None
    adjustment_reason: Optional[str] = None
    customer: str
    market: str
    edge: str
    not_a_difference: str
    channels: List[str]
    money_summary: str
    cheapest_test: CheapestTest
    green_gates: List[Gate]
    kill_switch: List[Gate]
    first_30_days: List[str]
    what_would_change: List[str]


SYSTEM = """You are the Writer in a business-idea checker. You turn a finished assessment \
into a short report a first-time founder can read in about three minutes. Plain words, \
short sentences, no jargon, no hype. Respectful and direct: never soften the verdict, and \
never upgrade it because the person is keen.

The verdict and the six area ratings were decided by fixed rules. You explain them; you do \
not change or question them.

Numbers: use only numbers that appear in the answers, findings or money figures given. \
Right after each one, put its tag in brackets: (verified), (estimated) or (assumed). The \
targets you set in the test, green gates and kill switch are plans, not facts, so they \
take no tag.

Write:
- verdict_reasons: two or three short sentences, at most 50 words in all, on why the \
verdict is what it is.
- area_lines: one line for each of the six areas, at most 20 words, explaining its \
rating with its main evidence. The rating is shown next to it, so do not start with the \
rating word. The verdict and all six lines must fit on one screen.
- idea_sentence: one sentence: what it is, who it is for, why they would choose it.
- adjusted_idea and adjustment_reason: if the alerts or weak areas point to a better \
version of the idea that the person has not taken, write that nearest version in one \
sentence and why in one sentence. For "Drop or rethink" this is required: the nearest \
version of the idea that could pass. Otherwise leave both empty.
- customer: who buys and what problem it solves for them (two sentences).
- market: its size and direction (two sentences).
- edge: the gap this fills (one or two sentences).
- not_a_difference: what the person may think is a difference but every competitor \
already claims (one sentence).
- channels: how to sell, three to five channels in priority order, each a short phrase.
- money_summary: two sentences on what the money figures mean.
- cheapest_test: one experiment that would settle the weakest point, with a budget and a \
time limit, sized to the person's budget.
- green_gates: three or four measures with the number that means keep going.
- kill_switch: two or three measures with the number that means stop.
- first_30_days: five to seven short, concrete steps.
- what_would_change: the one or two facts that would move the verdict up or down."""


def _prompt(session: dict, scores: dict) -> str:
    lines = ["VERDICT: %s (confidence %s; %d%% of the evidence is verified)" % (
        VERDICT_NAMES[scores["verdict"]], scores["confidence"], round(scores["verified_share"] * 100))]
    lines += ["Rule applied: " + r for r in scores["rule_reasons"]]
    lines.append("\nAREAS")
    for a in scores["areas"]:
        lines.append("- %s: %s (evidence mostly %s). Rule: %s" % (a["name"], a["rating"], a["basis"], a["rule"]))
        for s in a["signals"]:
            lines.append("   %s %s: %s %s" % (s["id"], s["value"], s["note"], s["fact_ids"] or ""))
    lines.append("\nMONEY FIGURES (worked out in code)")
    lines.append(json.dumps(scores["money"], ensure_ascii=False))
    lines.append("\nANSWERS")
    for q in QUESTIONS:
        a = session["answers"].get(q["id"])
        if a:
            lines.append("- %s\n   %s" % (a.get("question") or q["text"], _answer_text(a)))
    lines.append("\nFINDINGS")
    for f in session.get("facts") or []:
        lines.append("- %s [%s, %s]: %s" % (f["id"], f["topic"], f["tag"], f["claim"]))
    lines.append("\nALERTS")
    for a in session.get("alerts") or []:
        lines.append("- %s (%s, %s): %s Suggested: %s" % (a["key"], a["severity"], a.get("status"),
                                                         a["data_says"], a["suggestion"]))
    return "\n".join(lines)


def write(session: dict, scores: dict) -> Optional[dict]:
    result = ai.call(session, "write", SYSTEM + knowledge.for_role("writer"), _prompt(session, scores), Report,
                     max_tokens=16000, timeout=300.0, effort="medium")
    return result.model_dump() if result else None
