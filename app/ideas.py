"""Idea finding for the few-ideas and no-idea paths (brief section 4, Stage 2).

  rank()    - few ideas: a quick web check on each listed idea, rated against the
              person's Stage 1 answers, returned as a ranked shortlist.
  suggest() - no idea: five ideas that fit Stage 1, built from the problems and
              industries the person named. Can be asked again for five more.

The AI rates each idea on fixed 0-2 scales; the ranking itself is done here in
code, so the same ratings always give the same order. An idea that breaks a
non-negotiable always ranks below those that don't. An idea that is illegal or
unsafe is left out, with the reason shown.
"""

import re
from typing import List, Optional

from pydantic import BaseModel

from . import ai
from .checker import _answer_text

STAGE_ONE = [("q2", "Where"), ("q3", "Money they can put in"), ("q4", "Time each week"),
             ("q5", "Skills and access"), ("q6", "Needs to start earning"), ("q7", "Refuses to do")]
MAX_LISTED = 8


class Evidence(BaseModel):
    claim: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None


class RatedIdea(BaseModel):
    listed_as: Optional[str] = None      # rank: the person's line this rates
    title: str
    description: str
    unsafe_reason: Optional[str] = None
    budget_fit: int
    time_fit: int
    skills_fit: int
    timeline_fit: int
    demand: int
    breaks: List[str]
    reason: str
    evidence: List[Evidence]


class Ideas(BaseModel):
    ideas: List[RatedIdea]


RATING_RULES = """Rate each idea on these scales, 0, 1 or 2, against what the person told you:
- budget_fit: 2 if a small first version clearly fits the money they can put in, 0 if it clearly does not.
- time_fit: 2 if it fits the time they can give, 0 if it clearly needs much more.
- skills_fit: 2 if their skills or access give them a real head start, 0 if they have none that helps.
- timeline_fit: 2 if it can start earning when they need it to, 0 if it clearly cannot.
- demand: 2 if your search found clear signs people already pay for this where they live, 1 for some \
signs, 0 for none.
breaks: each of the person's non-negotiables (what they refuse to do) that the idea would break, in \
their words; [] if none.
reason: one plain line on why it ranks where it does: its main strength and main weakness.
evidence: one or two facts from your search with the page URL. Never make up a number, URL or brand.
title: a few words. description: one or two sentences: what it is, who it is for, why they would \
choose it, written so it can be tested as is.
unsafe_reason: if the idea is illegal or could seriously harm people, say why in one polite \
sentence; otherwise leave it empty."""

RANK_SYSTEM = """You are helping a person choose which of their business ideas to test first. \
For each idea they listed, do a quick web search on demand and competitors where they live, then \
rate it. Keep the person's idea; you may sharpen the wording but not change what it is. Return one \
entry per listed idea, with listed_as set to their line.

""" + RATING_RULES

SUGGEST_SYSTEM = """You are helping a person who has no business idea yet. Suggest five distinct, \
realistic ideas they could test, built from the problems they notice and the industries or \
customers they know, and fitting what they told you about money, time, skills, timing and limits. \
Prefer ideas that break none of their non-negotiables. Do a quick web search to check each one has \
real demand where they live. Leave listed_as empty.

""" + RATING_RULES


def _clamp(v: int) -> int:
    return max(0, min(2, int(v)))


def _stage_one(session: dict) -> str:
    lines = []
    for qid, label in STAGE_ONE:
        a = session["answers"].get(qid)
        if a:
            lines.append("- %s: %s" % (label, _answer_text(a)))
    return "\n".join(lines) or "(not given)"


def listed_ideas(session: dict) -> List[str]:
    a = session["answers"].get("q8_list") or {}
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
             for line in (a.get("value") or "").splitlines()]
    return [line for line in lines if line][:MAX_LISTED]


def _clean(found: Ideas, start: int) -> List[dict]:
    out = []
    for i, idea in enumerate(found.ideas):
        scores = {k: _clamp(getattr(idea, k)) for k in
                  ("budget_fit", "time_fit", "skills_fit", "timeline_fit", "demand")}
        evidence = [{"claim": e.claim.strip(), "source_url": e.source_url,
                     "source_name": e.source_name or (re.sub(r"^www\.", "", e.source_url.split("/")[2])
                                                      if e.source_url and e.source_url.count("/") >= 2 else None)}
                    for e in idea.evidence if e.claim.strip() and (e.source_url or "").startswith("http")]
        out.append({
            "id": "i%d" % (start + i + 1),
            "listed_as": (idea.listed_as or "").strip() or None,
            "title": idea.title.strip(),
            "description": idea.description.strip(),
            "reason": idea.reason.strip(),
            "breaks": [b.strip() for b in idea.breaks if b.strip()],
            "unsafe_reason": (idea.unsafe_reason or "").strip() or None,
            "scores": scores,
            "score": sum(scores.values()),
            "evidence": evidence[:2],
        })
    return out


def order(items: List[dict]) -> List[dict]:
    """Fixed ranking: safe ideas first, then no broken non-negotiables, then by score."""
    for position, item in enumerate(items):
        item.setdefault("position", position)
    return sorted(items, key=lambda x: (bool(x["unsafe_reason"]), bool(x["breaks"]), -x["score"], x["position"]))


def rank(session: dict) -> Optional[List[dict]]:
    ideas = listed_ideas(session)
    if not ideas:
        return None
    user = "WHAT THE PERSON TOLD YOU\n%s\n\nTHEIR IDEAS\n%s" % (
        _stage_one(session), "\n".join("- " + i for i in ideas))
    found = ai.call(session, "ideas:rank", RANK_SYSTEM, user, Ideas,
                    max_tokens=16000, timeout=420.0, effort="low", web=True)
    return order(_clean(found, 0)) if found else None


def suggest(session: dict, existing: List[dict]) -> Optional[List[dict]]:
    lines = ["WHAT THE PERSON TOLD YOU", _stage_one(session)]
    for qid, label in (("q8_problems", "Problems they notice"), ("q9", "Industries or customers they know")):
        a = session["answers"].get(qid)
        if a:
            lines.append("- %s: %s" % (label, _answer_text(a)))
    if existing:
        lines.append("\nALREADY SUGGESTED (suggest five different ones)")
        lines += ["- %s: %s" % (i["title"], i["description"]) for i in existing]
    found = ai.call(session, "ideas:suggest", SUGGEST_SYSTEM, "\n".join(lines), Ideas,
                    max_tokens=16000, timeout=420.0, effort="low", web=True)
    if not found:
        return None
    fresh = _clean(found, len(existing))
    for n, item in enumerate(fresh):
        item["position"] = len(existing) + n
    return fresh
