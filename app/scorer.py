"""Scorer (brief section 6): fixed verdict rules, applied in code.

Two parts:
  assess()  - one AI call that only gathers evidence: it answers a fixed list of
              yes / no / unclear questions for each area, citing fact ids, and
              pulls the money numbers out of the answers and findings.
  score()   - plain code. Turns that evidence into the six area ratings, the
              verdict and the confidence. No AI judgement here.

The assessment is saved with a fingerprint of the answers and findings it was
made from, and reused while they are unchanged, so the same answers and data
always give the same verdict (brief: honesty rules).

Tags on every number and finding:
  verified  - read on a source page
  estimated - worked out, or from a nearby market, with the reasoning shown
  assumed   - the person's own figure, or no evidence either way
"""

import hashlib
import json
import math
from typing import Dict, List, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from . import ai, cache, knowledge
from .checker import _answer_text
from .questions import QUESTIONS

AREAS = ["demand", "market", "competition", "money", "fit", "risk"]
AREA_NAMES = {
    "demand": "Demand", "market": "Market", "competition": "Competition",
    "money": "Money", "fit": "Fit", "risk": "Risk",
}
AREA_QUESTIONS = {
    "demand": "Do people already spend money or effort on this problem?",
    "market": "Is the customer group big enough and not shrinking?",
    "competition": "Is there a real gap, or is everyone already saying the same thing?",
    "money": "Does one sale leave enough after costs and winning the customer?",
    "fit": "Does it match your budget, time, skills and limits?",
    "risk": "Is there a legal, safety or dependency issue that could end it?",
}

# The fixed questions the Assessor answers. "yes" is always the good answer.
SIGNALS = {
    "D1": ("demand", "People already pay for products or services that solve this problem."),
    "D2": ("demand", "There is evidence of active buying or searching: sales volumes, review counts, search interest."),
    "M1": ("market", "The customer group is big enough to support this business at the planned scale."),
    "M2": ("market", "The customer group or category is stable or growing, not shrinking."),
    "C1": ("competition", "There is a real gap that current competitors do not fill."),
    "C2": ("competition", "The person's stated difference is not already claimed by competitors."),
    "C3": ("competition", "That difference would be hard for competitors to copy."),
    "F1": ("fit", "The idea as described breaks none of the person's non-negotiables (what they said they refuse to do)."),
    "F2": ("fit", "The time the person can give matches what the first stage needs."),
    "F3": ("fit", "The person's skills, contacts or access match what the business needs."),
    "F4": ("fit", "The business can start earning within the timeline the person needs."),
    "R1": ("risk", "No legal, safety or licensing issue was found that could stop the business."),
    "R2": ("risk", "The business does not depend on one platform, supplier or partner that could end it."),
}


# ---------- what the Assessor returns ----------

class Signal(BaseModel):
    id: str
    value: Literal["yes", "no", "unclear"]
    fact_ids: List[str]
    note: str


class Number(BaseModel):
    value: Optional[float] = None
    source: Literal["answer", "research", "none"] = "none"
    fact_ids: List[str] = []
    note: Optional[str] = None


class StartupItem(BaseModel):
    item: str
    amount: float
    source: Literal["answer", "research", "estimate"]
    fact_ids: List[str] = []
    note: Optional[str] = None


class Assessment(BaseModel):
    currency: str
    signals: List[Signal]
    price: Number
    cost_per_sale: Number
    customer_cost: Number
    purchases_per_year: Number
    lifetime_months: Number
    budget: Number
    startup_items: List[StartupItem]


SYSTEM = """You are the Assessor in a business-idea checker. You do not judge the idea. \
You gather evidence: you answer a fixed list of yes / no / unclear questions and pull out \
a few numbers, each tied to its source. Fixed rules in code then score the idea from what \
you return, so be literal and consistent: the same answers and findings must always give \
the same result.

QUESTIONS (one signal each, ids as given; "yes" is always the good answer)
%s

For each signal: value is "yes" or "no" only when the answers or findings clearly support \
it, otherwise "unclear". fact_ids are the findings you relied on (ids like C3 or M5); use \
[] when it rests only on the person's answers. note is one short plain sentence of why.

NUMBERS, all in one currency (give its ISO code in currency, for example INR):
- price: what the person will charge for one sale.
- cost_per_sale: the full cost of delivering one sale: making or buying it, packing, \
delivery and channel fees, but not advertising. Use the person's figure if they gave one \
(source "answer"), unless they only gave part of it; then add the missing parts from the \
findings and say so in note.
- customer_cost: the cost to win one paying customer (advertising and promotion per new \
customer), from the person's answers or the findings.
- purchases_per_year: how many times one customer buys in a year.
- lifetime_months: how many months one customer keeps buying.
- budget: the money the person said they can put in.
- startup_items: always give three to six items for what a small first stage costs \
(for example first stock, packaging and label design, registrations or tests, launch \
advertising). Use source "answer" or "research" where you can; otherwise work the amount \
out from the findings and answers (for example unit cost times a small first order) with \
source "estimate", and show the working in note.
For the other numbers, source is "answer" for the person's own figures and "research" \
for figures taken from the findings, with their fact_ids. If neither gives a basis, \
leave value empty with source "none". Never make up a number without showing how you \
got it."""


def _system() -> str:
    lines = ["- %s (%s): %s" % (sid, area, text) for sid, (area, text) in SIGNALS.items()]
    return SYSTEM % "\n".join(lines) + knowledge.for_role("assessor")


def _prompt(session: dict) -> str:
    lines = ["ANSWERS"]
    for q in QUESTIONS:
        a = session["answers"].get(q["id"])
        if a:
            lines.append("- %s: %s\n   %s" % (q["id"], a.get("question") or q["text"], _answer_text(a)))
    lines.append("\nFINDINGS")
    for f in session.get("facts") or []:
        reason = " (estimate: %s)" % f["reasoning"] if f.get("reasoning") and f.get("tag") == "estimated" else ""
        lines.append("- %s [%s, %s]: %s%s" % (f["id"], f["topic"], f["tag"], f["claim"], reason))
    if not session.get("facts"):
        lines.append("(none)")
    return "\n".join(lines)


# Bump when the Assessor's questions or instructions change, so saved
# assessments made under the old ones are not reused.
ASSESS_VERSION = 5


def fingerprint(session: dict) -> str:
    """Changes whenever an answer or a finding changes."""
    answers = {qid: [a.get("value"), a.get("other"), a.get("dont_know"),
                     (a.get("follow_up") or {}).get("answer")]
               for qid, a in sorted(session["answers"].items())}
    # Sorted, so the order the research jobs happened to finish in doesn't matter.
    facts = sorted([f["id"], f["claim"], f["tag"]] for f in session.get("facts") or [])
    raw = json.dumps({"v": ASSESS_VERSION, "answers": answers, "facts": facts}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def assess(session: dict) -> Optional[dict]:
    """The evidence for these answers and findings: reused if already gathered, so the
    same answers and data always give the same verdict, in any session."""
    name = "assess-" + fingerprint(session)
    hit = cache.get(name)
    if hit:
        return hit["value"]
    result = ai.call(session, "assess", _system(), _prompt(session), Assessment,
                     max_tokens=12000, timeout=240.0, effort="medium")
    if result is None:
        return None
    data = result.model_dump()
    cache.put(name, data)
    return data


# ---------- scoring: fixed rules, plain code ----------

def _facts_by_id(session: dict) -> Dict[str, dict]:
    return {f["id"]: f for f in session.get("facts") or []}


def _tag_for(source: str, fact_ids: List[str], facts: Dict[str, dict]) -> str:
    """verified only if every cited finding is verified; the person's own figures are assumed."""
    cited = [facts[i] for i in fact_ids if i in facts]
    if source == "estimate":
        return "estimated"
    if source == "research" and cited:
        return "verified" if all(f["tag"] == "verified" for f in cited) else "estimated"
    return "assumed"


def _number(n: dict, facts: Dict[str, dict]) -> dict:
    value = n.get("value")
    source = n.get("source") or "none"
    if value is None or source == "none":
        return {"value": None, "tag": None, "fact_ids": [], "note": n.get("note")}
    ids = [i for i in n.get("fact_ids") or [] if i in facts]
    return {"value": value, "tag": _tag_for(source, ids, facts), "fact_ids": ids, "note": n.get("note")}


def _weakest(*tags: Optional[str]) -> str:
    order = {"assumed": 0, "estimated": 1, "verified": 2}
    known = [t for t in tags if t]
    return min(known, key=order.get) if known else "assumed"


def _spoken_to(session: dict) -> str:
    a = session["answers"].get("q13") or {}
    value = (a.get("value") or "") if not a.get("dont_know") else ""
    if "paying" in value.lower():
        return "paying"
    if value.lower().startswith("many"):
        return "many"
    if "few" in value.lower():
        return "few"
    return "none"


def money_numbers(assessment: dict, facts: Dict[str, dict], session: dict) -> dict:
    """The unit economics, worked out in code from the tagged numbers."""
    n = {k: _number(assessment[k], facts) for k in
         ("price", "cost_per_sale", "customer_cost", "purchases_per_year", "lifetime_months", "budget")}
    price, cost, cac = n["price"]["value"], n["cost_per_sale"]["value"], n["customer_cost"]["value"]
    per_year, months = n["purchases_per_year"]["value"], n["lifetime_months"]["value"]
    out = {"currency": assessment.get("currency"), **n}

    items = []
    for it in assessment.get("startup_items") or []:
        ids = [i for i in it.get("fact_ids") or [] if i in facts]
        items.append({"item": it["item"], "amount": it["amount"], "note": it.get("note"),
                      "fact_ids": ids, "tag": _tag_for(it["source"], ids, facts)})
    out["startup_items"] = items
    out["startup_total"] = {"value": round(sum(i["amount"] for i in items), 2) if items else None,
                            "tag": _weakest(*[i["tag"] for i in items]) if items else None}

    if price is not None and cost is not None:
        keep = round(price - cost, 2)
        out["keep_per_sale"] = {"value": keep, "tag": _weakest(n["price"]["tag"], n["cost_per_sale"]["tag"])}
        out["margin"] = round(keep / price, 3) if price else None
        if cac is not None:
            out["first_sale_after_customer_cost"] = {
                "value": round(keep - cac, 2),
                "tag": _weakest(out["keep_per_sale"]["tag"], n["customer_cost"]["tag"])}
        purchases = 1.0
        if per_year is not None and months is not None:
            purchases = max(1.0, round(per_year * months / 12.0, 1))
        out["lifetime_purchases"] = {"value": purchases,
                                     "tag": _weakest(n["purchases_per_year"]["tag"], n["lifetime_months"]["tag"])
                                     if per_year is not None and months is not None else "assumed"}
        out["lifetime_keep"] = {"value": round(keep * purchases, 2),
                                "tag": _weakest(out["keep_per_sale"]["tag"], out["lifetime_purchases"]["tag"])}
        if cac:
            out["lifetime_to_customer_cost"] = round(keep * purchases / cac, 2)
        total = out["startup_total"]["value"]
        if total and keep > 0:
            out["sales_to_cover_startup"] = {"value": int(math.ceil(total / keep)),
                                             "tag": _weakest(out["startup_total"]["tag"], out["keep_per_sale"]["tag"])}
    return out


def _signal_map(assessment: dict, facts: Dict[str, dict]) -> Dict[str, dict]:
    got = {s["id"]: s for s in assessment.get("signals") or []}
    out = {}
    for sid, (area, text) in SIGNALS.items():
        s = got.get(sid) or {"value": "unclear", "fact_ids": [], "note": "Not assessed."}
        ids = [i for i in s.get("fact_ids") or [] if i in facts]
        out[sid] = {"id": sid, "area": area, "question": text, "value": s["value"],
                    "fact_ids": ids, "note": s.get("note") or "",
                    "tag": _tag_for("research", ids, facts) if ids else "assumed"}
    return out


def _basis(tags: List[str]) -> str:
    """How an area's evidence stands overall: verified, estimated or assumed."""
    if not tags:
        return "assumed"
    verified = sum(t == "verified" for t in tags)
    if verified and verified * 2 >= len(tags):
        return "verified"
    return "estimated" if any(t in ("verified", "estimated") for t in tags) else "assumed"


def score(assessment: dict, session: dict) -> dict:
    facts = _facts_by_id(session)
    sig = _signal_map(assessment, facts)
    money = money_numbers(assessment, facts, session)
    v = {sid: s["value"] for sid, s in sig.items()}
    # The verdict rests only on the answers and the (saved) assessment, never on
    # the Checker's alerts, so the same answers and data always give the same verdict.
    broken = v["F1"] == "no"
    spoken = _spoken_to(session)
    areas = {}

    def area(name, rating, why, tags, extra=None):
        areas[name] = {"area": name, "name": AREA_NAMES[name], "question": AREA_QUESTIONS[name],
                       "rating": rating, "rule": why, "basis": _basis(tags),
                       "signals": [s for s in sig.values() if s["area"] == name], **(extra or {})}

    def tags_of(*sids):
        return [sig[s]["tag"] for s in sids]

    # Demand
    if v["D1"] == "no":
        r, why = "weak", "No sign that people already pay to solve this problem."
    elif v["D1"] == "yes" and spoken == "paying":
        r, why = "strong", "People already pay for this, and you already have paying customers."
    elif v["D1"] == "yes" and v["D2"] == "yes" and spoken == "many":
        r, why = "strong", "People already pay for this, buying is active, and you have spoken to many buyers."
    else:
        r, why = "okay", "Some evidence of demand, but not yet proven with your own buyers."
    area("demand", r, why, tags_of("D1", "D2"), {"spoken_to": spoken})

    # Market
    if v["M1"] == "no" or v["M2"] == "no":
        r, why = "weak", "The customer group looks too small or is shrinking."
    elif v["M1"] == "yes" and v["M2"] == "yes":
        r, why = "strong", "The customer group is big enough and not shrinking."
    else:
        r, why = "okay", "The size or direction of the market is not clear."
    area("market", r, why, tags_of("M1", "M2"))

    # Competition
    if v["C1"] == "no" and v["C2"] == "no":
        r, why = "weak", "No clear gap, and competitors already make the same claims."
    elif v["C1"] == "yes" and v["C2"] == "yes" and v["C3"] == "yes":
        r, why = "strong", "A real gap, a difference competitors don't claim, and hard to copy."
    else:
        r, why = "okay", "Some room to stand out, but the difference is not yet clear or lasting."
    area("competition", r, why, tags_of("C1", "C2", "C3"))

    # Money: worked out from the numbers
    money_tags = [money[k]["tag"] for k in ("price", "cost_per_sale", "customer_cost") if money[k]["tag"]]
    keep = (money.get("keep_per_sale") or {}).get("value")
    margin = money.get("margin")
    ratio = money.get("lifetime_to_customer_cost")
    if keep is None:
        r, why = "okay", "Price or cost per sale is missing, so the money cannot be checked."
    elif keep <= 0:
        r, why = "weak", "Each sale costs as much as or more than its price."
    elif margin is not None and margin < 0.10:
        r, why = "weak", "Less than 10% of the price is left after costs."
    elif ratio is not None and ratio < 1:
        r, why = "weak", "A customer's lifetime profit does not cover the cost of winning them."
    elif ratio is not None and ratio >= 3 and margin is not None and margin >= 0.40:
        r, why = "strong", "40% or more of the price is kept, and a customer is worth 3 times or more what it costs to win them."
    else:
        r, why = "okay", "Each sale makes money, but the margin or the cost to win a customer leaves little room."
    area("money", r, why, money_tags)

    # Fit
    budget = money["budget"]["value"]
    startup = money["startup_total"]["value"]
    within = None if budget is None or startup is None else startup <= budget
    fit_tags = tags_of("F1", "F2", "F3", "F4") + ([money["startup_total"]["tag"]] if startup else [])
    if broken:
        r, why = "weak", "The idea breaks one of your non-negotiables."
    elif within is False:
        r, why = "weak", "The first stage costs more than the money you can put in."
    elif within and v["F2"] == "yes" and v["F3"] == "yes" and v["F4"] == "yes":
        r, why = "strong", "It fits your budget, time, skills and timeline."
    else:
        r, why = "okay", "It mostly fits, with some gaps in budget, time, skills or timeline."
    area("fit", r, why, fit_tags, {"within_budget": within, "breaks_non_negotiable": broken})

    # Risk
    blocking = v["R1"] == "no"
    if blocking:
        r, why = "weak", "A legal, safety or licensing issue could stop the business."
    elif v["R1"] == "yes" and v["R2"] == "yes":
        r, why = "strong", "No blocking legal issue and no single dependency that could end it."
    else:
        r, why = "okay", "No blocker found, but a rule or a dependency needs checking."
    area("risk", r, why, tags_of("R1", "R2"), {"blocking": blocking})

    # How much of the evidence is verified
    used = set()
    for s in sig.values():
        used.update(s["fact_ids"])
    tags = [facts[i]["tag"] for i in used]
    tags += [money[k]["tag"] for k in ("price", "cost_per_sale", "customer_cost", "purchases_per_year",
                                       "lifetime_months") if money[k]["tag"]]
    share = round(sum(t == "verified" for t in tags) / len(tags), 2) if tags else 0.0
    mostly_unverified = share < 0.30

    # The verdict, by the brief's rules, in order
    weak = [a for a in AREAS if areas[a]["rating"] == "weak"]
    reasons = []
    if broken:
        verdict = "drop"
        reasons.append("It breaks one of your non-negotiables.")
    elif blocking:
        verdict = "drop"
        reasons.append("There is a blocking legal, safety or licensing risk.")
    elif any(areas[a]["rating"] == "weak" and areas[a]["basis"] == "verified" for a in ("demand", "money")):
        verdict = "drop"
        reasons.append("Demand or Money is weak on verified data.")
    elif len(weak) >= 3:
        verdict = "drop"
        reasons.append("Three or more areas are weak.")
    elif not weak and areas["demand"]["rating"] == "strong" and areas["money"]["rating"] == "strong":
        verdict = "pursue"
        reasons.append("No weak areas, and Demand and Money are both strong.")
    else:
        verdict = "test"
        if weak:
            reasons.append("%s %s weak, which a cheap test could settle." % (
                _join([AREA_NAMES[a] for a in weak]), "is" if len(weak) == 1 else "are"))
        else:
            reasons.append("No weak areas, but Demand and Money are not both strong yet.")

    capped = False
    if mostly_unverified and verdict == "pursue":
        verdict, capped = "test", True
        reasons.append("Capped at Test small first: most of the data is estimated or assumed.")

    if share >= 0.60 and not mostly_unverified:
        confidence = "high"
    elif share >= 0.30:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "verified_share": share,
        "capped": capped,
        "rule_reasons": reasons,
        "weak_areas": weak,
        "areas": [areas[a] for a in AREAS],
        "money": money,
    }


def _join(names: List[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
