"""Runs the brief's test plan (section 11) through the running app, end to end.

Start the app first (./run.sh), then:
    .venv/bin/python tests/test_plan.py            # all cases
    .venv/bin/python tests/test_plan.py ref good1  # some cases

Each case answers the form the way a person would (skipping any follow-up
questions), waits for the report, and prints the verdict, ratings and alerts.
Every run is saved in reports/ like any other, so it shows in History.
Results are also written to tests/results-<time>.json.
"""

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = "http://127.0.0.1:8765"
ORDER = ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q10", "q11", "q12", "q13",
         "q14", "q15", "q16", "q17", "q18", "q19", "q20"]
ONE = "I have one idea"

REFERENCE = {
    "q1": ONE, "q2": "India, Mumbai", "q3": "10 lakh rupees", "q4": "Full time, team of three",
    "q5": "Marketplace selling experience and contacts with white-label manufacturers",
    "q6": "From day 1",
    "q7": (["No building new technology"], "No R&D, white label only, start with one product, online marketplaces first, revenue from day 1, repeat purchase"),
    "q8": "A natural, plant-based baby laundry detergent, white-label manufactured, sold online first in Indian metro cities to mothers aged 27 to 40.",
    "q10": "Mothers aged 27 to 40 in Indian metro cities with a baby under 2",
    "q11": "Regular detergents can irritate baby skin. Today they buy Mamaearth or Himalaya baby detergent online, or just use regular detergent.",
    "q12": "Plant-based and gentle on baby skin",
    "q13": "A few people", "q14": "Mamaearth, Himalaya, Mother Sparsh", "q15": "Mamaearth",
    "q16": "Plant-based, baby-safe, no harsh chemicals", "q17": "₹449 for 1 litre",
    "q18": (["Online marketplaces"], None), "q19": "Every month while the baby is small",
    "q20": "About ₹170 per bottle including manufacturing, packaging, marketplace fees and delivery",
}

CASES = {
    "ref": ("Reference: baby detergent (run 1)", "test", REFERENCE),
    "ref2": ("Reference: baby detergent (run 2, same answers)", "test", REFERENCE),
    "bad1": ("Likely bad: DVD rental shop", "drop", {
        "q1": ONE, "q2": "India, Bangalore", "q3": "8 lakh rupees", "q4": "Full time",
        "q5": "I love films and know a lot about them", "q6": "Within 6 months", "q7": ([], None),
        "q8": "A DVD and Blu-ray rental shop in Koramangala, Bangalore, renting new films for ₹50 a night.",
        "q10": "Families who like watching films at home",
        "q11": "They want to watch new films at home. Today they use Netflix, Prime Video and JioHotstar.",
        "q12": "Discs have better picture quality and I give personal recommendations",
        "q13": "No", "q14": None, "q15": None, "q16": "Personal recommendations from me",
        "q17": "₹50 per disc per night", "q18": (["A physical shop or location"], None),
        "q19": "Maybe once a month", "q20": "About ₹15 per rental, plus ₹60,000 a month shop rent",
    }),
    "bad2": ("Likely bad: pet social network app on a tiny budget", "drop", {
        "q1": ONE, "q2": "India, Delhi", "q3": "50,000 rupees", "q4": "5 hours a week",
        "q5": "I am good at social media", "q6": "From day 1",
        "q7": (["No building new technology", "No employees"], None),
        "q8": "A social network app just for pet owners in India, with its own feed, chat and marketplace, built from scratch.",
        "q10": "Anyone with a pet", "q11": "Pet owners want to connect. Today they use Instagram and WhatsApp groups.",
        "q12": "It is only for pets", "q13": "No", "q14": "Instagram", "q15": None,
        "q16": "Only for pets", "q17": "Free, I will earn from ads later",
        "q18": (["Social media"], None), "q19": "Every day", "q20": None,
    }),
    "good1": ("Likely good: CA-run GST bookkeeping for Pune shops", "pursue/test", {
        "q1": ONE, "q2": "India, Pune", "q3": "1.5 lakh rupees", "q4": "Full time",
        "q5": "I am a chartered accountant with 8 years at a mid-size firm, and I know many shop owners in Pune",
        "q6": "Within 6 months", "q7": (["No inventory", "No building new technology"], None),
        "q8": "A monthly bookkeeping and GST filing service for small shops and traders in Pune, run by me as a chartered accountant, for a fixed monthly fee.",
        "q10": "Owners of kirana and hardware shops in Pune with ₹50 lakh to ₹2 crore turnover",
        "q11": "They must file GST returns every month and keep books. Today they use a local accountant who is slow and charges per filing, or do it themselves and get late fees.",
        "q12": "A fixed monthly fee, updates on WhatsApp and filings always on time",
        "q13": "Many", "q14": "Local accountants, ClearTax, Vakilsearch", "q15": None,
        "q16": "An on-time guarantee and a WhatsApp-first service in Marathi and Hindi",
        "q17": "₹2,500 per month per shop", "q18": (["Word of mouth and referrals", "Direct sales and outreach"], None),
        "q19": "Every month, for years", "q20": "About ₹600 of my time and software per client per month",
    }),
    "good2": ("Likely good: tiffin subscription by an experienced home cook", "pursue/test", {
        "q1": ONE, "q2": "India, Bangalore", "q3": "3 lakh rupees", "q4": "Full time",
        "q5": "I have run a home kitchen for 4 years, cook for 40 people a day and have an FSSAI-registered kitchen",
        "q6": "Within 6 months", "q7": (["No borrowing"], None),
        "q8": "A lunch tiffin subscription delivering healthy home-style North Indian meals to office workers in Whitefield tech parks for ₹3,000 a month.",
        "q10": "Software engineers aged 24 to 35 from North India living in PGs near Whitefield",
        "q11": "They want healthy home-style food every day. Today they order on Swiggy or Zomato, which is costly and oily, or eat at the office canteen.",
        "q12": "Cheaper than ordering every day, healthier, and tastes like home",
        "q13": "I already have paying customers", "q14": "Swiggy, Zomato, local tiffin services, office canteens",
        "q15": None, "q16": "Home-style North Indian food on a monthly plan with steady quality",
        "q17": "₹3,000 per month for 22 lunches", "q18": (["Word of mouth and referrals", "Social media"], None),
        "q19": "Every month", "q20": "About ₹75 per meal including ingredients, packaging and delivery",
    }),
}


def call(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def answer(value):
    if value is None:
        return {"value": None, "other": None, "dont_know": True}
    if isinstance(value, tuple):
        return {"value": value[0], "other": value[1], "dont_know": False}
    return {"value": value, "other": None, "dont_know": False}


def skip_follow_ups(sid):
    session = call("GET", "/api/sessions/%s" % sid)
    for qid, a in session["answers"].items():
        if (a.get("follow_up") or {}).get("status") == "pending":
            call("PUT", "/api/sessions/%s/answers/%s/follow_up" % (sid, qid), {"value": None})


def run(key):
    title, expected, answers = CASES[key]
    started = time.time()
    sid = call("POST", "/api/sessions")["id"]
    for qid in ORDER:
        if qid not in answers:
            continue
        result = call("PUT", "/api/sessions/%s/answers/%s" % (sid, qid), answer(answers[qid]))
        if result["session"].get("declined"):
            return {"case": key, "title": title, "expected": expected, "id": sid, "declined": result["session"]["declined"]}
        time.sleep(1.5)               # roughly a person's pace
        skip_follow_ups(sid)
    for _ in range(3):
        session = call("POST", "/api/sessions/%s/complete" % sid)
        if session.get("completed"):
            break
        skip_follow_ups(sid)
    report = {"status": "preparing"}
    deadline = time.time() + 900
    while report.get("status") == "preparing" and time.time() < deadline:
        time.sleep(5)
        report = call("GET", "/api/sessions/%s/report" % sid)
    out = {"case": key, "title": title, "expected": expected, "id": sid,
           "minutes": round((time.time() - started) / 60, 1), "status": report.get("status")}
    if report.get("status") == "done":
        s = report["scores"]
        out.update(verdict=s["verdict"], confidence=s["confidence"], verified_share=s["verified_share"],
                   ratings={a["area"]: a["rating"] for a in s["areas"]}, rule=s["rule_reasons"],
                   alerts=["%s:%s%s" % (a["severity"], a["key"], " (fixed)" if a.get("status") == "resolved" else "")
                           for a in report.get("alerts") or []],
                   adjusted=(report.get("text") or {}).get("adjusted_idea"),
                   usage=report.get("usage"))
    else:
        out["error"] = report.get("error")
    return out


def main(keys):
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, keys))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(__file__).parent / ("results-%s.json" % stamp)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        print("\n== %s  [expected %s]" % (r["title"], r["expected"]))
        for k in ("id", "minutes", "status", "verdict", "confidence", "verified_share", "ratings", "rule",
                  "alerts", "adjusted", "usage", "declined", "error"):
            if r.get(k) is not None:
                print("  %-15s %s" % (k, r[k]))
    print("\nSaved to", path)


if __name__ == "__main__":
    main(sys.argv[1:] or list(CASES))
