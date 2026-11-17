#!/usr/bin/env python3
"""Fixed verdict rules for the business-idea-checker skill.

Usage:
    python score.py evidence.json          # prints a readable summary
    python score.py evidence.json --json   # prints the full result as JSON

Input format: see references/verdict-rules.md. Plain Python 3, no packages needed.
The same evidence always gives the same verdict: no AI judgement happens here.
"""

import json
import math
import sys

AREAS = ["demand", "market", "competition", "money", "fit", "risk"]
NAMES = {"demand": "Demand", "market": "Market", "competition": "Competition",
         "money": "Money", "fit": "Fit", "risk": "Risk"}
SIGNAL_AREA = {"D1": "demand", "D2": "demand", "M1": "market", "M2": "market",
               "C1": "competition", "C2": "competition", "C3": "competition",
               "F1": "fit", "F2": "fit", "F3": "fit", "F4": "fit", "R1": "risk", "R2": "risk"}
VERDICTS = {"pursue": "Pursue", "test": "Test small first", "drop": "Drop or rethink"}
ORDER = {"assumed": 0, "estimated": 1, "verified": 2}


def weakest(*tags):
    known = [t for t in tags if t in ORDER]
    return min(known, key=ORDER.get) if known else "assumed"


def basis(tags):
    tags = [t for t in tags if t]
    if not tags:
        return "assumed"
    verified = sum(t == "verified" for t in tags)
    if verified and verified * 2 >= len(tags):
        return "verified"
    return "estimated" if any(t in ("verified", "estimated") for t in tags) else "assumed"


def join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def num(money, key):
    item = money.get(key) or {}
    value = item.get("value")
    return {"value": value, "tag": (item.get("tag") or "assumed") if value is not None else None}


def money_figures(ev):
    m = ev.get("money") or {}
    n = {k: num(m, k) for k in ("price", "cost_per_sale", "customer_cost", "purchases_per_year",
                                "lifetime_months", "budget")}
    out = dict(n)
    items = [i for i in ev.get("startup_items") or [] if i.get("amount")]
    total = round(sum(i["amount"] for i in items), 2) if items else None
    out["startup_total"] = {"value": total, "tag": weakest(*[i.get("tag") for i in items]) if items else None}
    price, cost, cac = n["price"]["value"], n["cost_per_sale"]["value"], n["customer_cost"]["value"]
    if price is not None and cost is not None:
        keep = round(price - cost, 2)
        out["keep_per_sale"] = {"value": keep, "tag": weakest(n["price"]["tag"], n["cost_per_sale"]["tag"])}
        out["margin"] = round(keep / price, 3) if price else None
        per_year, months = n["purchases_per_year"]["value"], n["lifetime_months"]["value"]
        purchases = max(1.0, round(per_year * months / 12.0, 1)) if per_year is not None and months is not None else 1.0
        out["purchases_per_customer"] = {"value": purchases,
                                         "tag": weakest(n["purchases_per_year"]["tag"], n["lifetime_months"]["tag"])
                                         if per_year is not None and months is not None else "assumed"}
        out["customer_worth"] = {"value": round(keep * purchases, 2),
                                 "tag": weakest(out["keep_per_sale"]["tag"], out["purchases_per_customer"]["tag"])}
        if cac is not None:
            out["first_sale_after_customer_cost"] = {"value": round(keep - cac, 2),
                                                     "tag": weakest(out["keep_per_sale"]["tag"], n["customer_cost"]["tag"])}
            if cac > 0:
                out["worth_to_customer_cost"] = round(keep * purchases / cac, 2)
        if total and keep > 0:
            out["sales_to_cover_startup"] = {"value": int(math.ceil(total / keep)),
                                             "tag": weakest(out["startup_total"]["tag"], out["keep_per_sale"]["tag"])}
    return out


def score(ev):
    raw = ev.get("signals") or {}
    sig = {}
    for sid, area in SIGNAL_AREA.items():
        s = raw.get(sid) or {}
        value = s.get("value") if s.get("value") in ("yes", "no", "unclear") else "unclear"
        sig[sid] = {"id": sid, "area": area, "value": value, "tag": s.get("tag") or "assumed", "note": s.get("note", "")}
    v = {k: s["value"] for k, s in sig.items()}
    spoken = ev.get("spoken_to") or "none"
    m = money_figures(ev)
    areas = {}

    def area(name, rating, rule, tags, **extra):
        areas[name] = {"area": name, "name": NAMES[name], "rating": rating, "rule": rule, "basis": basis(tags),
                       "signals": [s for s in sig.values() if s["area"] == name], **extra}

    def tags(*ids):
        return [sig[i]["tag"] for i in ids]

    if v["D1"] == "no":
        r, why = "weak", "No sign that people already pay to solve this problem."
    elif v["D1"] == "yes" and spoken == "paying":
        r, why = "strong", "People already pay for this, and you already have paying customers."
    elif v["D1"] == "yes" and v["D2"] == "yes" and spoken == "many":
        r, why = "strong", "People already pay for this, buying is active, and you have spoken to many buyers."
    else:
        r, why = "okay", "Some evidence of demand, but not yet proven with your own buyers."
    area("demand", r, why, tags("D1", "D2"))

    if v["M1"] == "no" or v["M2"] == "no":
        r, why = "weak", "The customer group looks too small or is shrinking."
    elif v["M1"] == "yes" and v["M2"] == "yes":
        r, why = "strong", "The customer group is big enough and not shrinking."
    else:
        r, why = "okay", "The size or direction of the market is not clear."
    area("market", r, why, tags("M1", "M2"))

    if v["C1"] == "no" and v["C2"] == "no":
        r, why = "weak", "No clear gap, and competitors already make the same claims."
    elif v["C1"] == "yes" and v["C2"] == "yes" and v["C3"] == "yes":
        r, why = "strong", "A real gap, a difference competitors don't claim, and hard to copy."
    else:
        r, why = "okay", "Some room to stand out, but the difference is not yet clear or lasting."
    area("competition", r, why, tags("C1", "C2", "C3"))

    keep = (m.get("keep_per_sale") or {}).get("value")
    margin, ratio = m.get("margin"), m.get("worth_to_customer_cost")
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
    area("money", r, why, [m[k]["tag"] for k in ("price", "cost_per_sale", "customer_cost") if m[k]["tag"]])

    budget, startup = m["budget"]["value"], m["startup_total"]["value"]
    within = None if budget is None or startup is None else startup <= budget
    broken = v["F1"] == "no"
    if broken:
        r, why = "weak", "The idea breaks one of your non-negotiables."
    elif within is False:
        r, why = "weak", "The first stage costs more than the money you can put in."
    elif within and v["F2"] == "yes" and v["F3"] == "yes" and v["F4"] == "yes":
        r, why = "strong", "It fits your budget, time, skills and timeline."
    else:
        r, why = "okay", "It mostly fits, with some gaps in budget, time, skills or timeline."
    area("fit", r, why, tags("F1", "F2", "F3", "F4") + ([m["startup_total"]["tag"]] if startup else []),
         within_budget=within)

    blocking = v["R1"] == "no"
    if blocking:
        r, why = "weak", "A legal, safety or licensing issue could stop the business."
    elif v["R1"] == "yes" and v["R2"] == "yes":
        r, why = "strong", "No blocking legal issue and no single dependency that could end it."
    else:
        r, why = "okay", "No blocker found, but a rule or a dependency needs checking."
    area("risk", r, why, tags("R1", "R2"))

    evidence = [s["tag"] for s in sig.values() if s["value"] != "unclear"]
    evidence += [m[k]["tag"] for k in ("price", "cost_per_sale", "customer_cost", "purchases_per_year",
                                       "lifetime_months") if m[k]["tag"]]
    share = round(sum(t == "verified" for t in evidence) / len(evidence), 2) if evidence else 0.0

    weak = [a for a in AREAS if areas[a]["rating"] == "weak"]
    reasons = []
    if broken:
        verdict = "drop"; reasons.append("It breaks one of your non-negotiables.")
    elif blocking:
        verdict = "drop"; reasons.append("There is a blocking legal, safety or licensing risk.")
    elif any(areas[a]["rating"] == "weak" and areas[a]["basis"] == "verified" for a in ("demand", "money")):
        verdict = "drop"; reasons.append("Demand or Money is weak on verified data.")
    elif len(weak) >= 3:
        verdict = "drop"; reasons.append("Three or more areas are weak.")
    elif len(weak) == 2 and any(areas[a]["basis"] == "verified" for a in weak):
        verdict = "drop"
        reasons.append("%s are weak, and verified data already shows it, so a cheap test could not settle it."
                       % join([NAMES[a] for a in weak]))
    elif not weak and areas["demand"]["rating"] == "strong" and areas["money"]["rating"] == "strong":
        verdict = "pursue"; reasons.append("No weak areas, and Demand and Money are both strong.")
    else:
        verdict = "test"
        if weak:
            reasons.append("%s %s weak, which a cheap test could settle."
                           % (join([NAMES[a] for a in weak]), "is" if len(weak) == 1 else "are"))
        else:
            reasons.append("No weak areas, but Demand and Money are not both strong yet.")

    capped = False
    if verdict == "pursue" and share < 0.30:
        verdict, capped = "test", True
        reasons.append("Capped at Test small first: most of the data is estimated or assumed.")
    confidence = "high" if share >= 0.60 else "medium" if share >= 0.30 else "low"

    return {"verdict": verdict, "verdict_name": VERDICTS[verdict], "confidence": confidence,
            "verified_share": share, "capped": capped, "reasons": reasons, "weak_areas": weak,
            "areas": [areas[a] for a in AREAS], "money": m}


def summary(res):
    cur_lines = []
    m = res["money"]
    for label, key in (("Kept per sale", "keep_per_sale"), ("Customer worth over their lifetime", "customer_worth"),
                       ("First sale after the cost to win the customer", "first_sale_after_customer_cost"),
                       ("Sales needed to cover start-up costs", "sales_to_cover_startup"),
                       ("Start-up total", "startup_total")):
        item = m.get(key)
        if item and item.get("value") is not None:
            value = item["value"]
            value = int(value) if float(value).is_integer() else value
            cur_lines.append("  %-46s %s (%s)" % (label, format(value, ","), item["tag"]))
    if m.get("margin") is not None:
        cur_lines.append("  %-46s %d%%" % ("Margin", round(m["margin"] * 100)))
    if m.get("worth_to_customer_cost") is not None:
        cur_lines.append("  %-46s %sx" % ("Customer worth / cost to win them", m["worth_to_customer_cost"]))
    lines = ["VERDICT: %s (confidence %s; %d%% of evidence verified)" % (
        res["verdict_name"], res["confidence"], round(res["verified_share"] * 100))]
    lines += ["  " + r for r in res["reasons"]]
    lines.append("\nSCORECARD")
    for a in res["areas"]:
        lines.append("  %-12s %-7s %s [evidence mostly %s]" % (a["name"], a["rating"], a["rule"], a["basis"]))
    if cur_lines:
        lines.append("\nMONEY (%s)" % (res["money"].get("currency") or ""))
        lines += cur_lines
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        ev = json.load(f)
    res = score(ev)
    res["money"]["currency"] = ev.get("currency")
    print(json.dumps(res, indent=2, ensure_ascii=False) if "--json" in sys.argv else summary(res))


if __name__ == "__main__":
    main()
