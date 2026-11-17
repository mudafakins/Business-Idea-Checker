"""Researcher (brief section 8): background web research, written to the fact store.

Two research jobs run side by side once the idea is described:
  competitors - real competitors, their prices and their claims. Used by
                question 15 (which is closest) and question 17 (what to charge).
  market      - market size and direction, demand signals, typical costs, how
                long customers keep buying, and rules or licences that apply.
The Checker compares the person's answers against both.

Research rules, from the brief, and where they are enforced:
  - Every number traces to a source, or is tagged as an estimate with the
    reasoning shown.                         -> prompt, and _clean() drops
                                                prices with no source page
  - Never fill a gap with an invented number. -> prompt; missing prices stay empty
  - When local data is thin, use the closest comparable market and say so.
                                              -> prompt; shown as market_note
  - The price range is worked out here in code from the cited prices, not
    taken from the AI, so it is the same every time for the same findings.

The fact store is session["facts"]: one entry per finding, each with its value,
source and tag (verified, estimated or assumed).
"""

import re
from datetime import date
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel
from typing_extensions import Literal

from . import ai

JOB = "competitors"
IDEA_QUESTION = "q8"


# ---------- what the AI returns ----------

class Competitor(BaseModel):
    brand: str
    product: str
    price: Optional[float] = None
    currency: Optional[str] = None
    pack_size: Optional[float] = None
    pack_unit: Optional[Literal["ml", "l", "g", "kg", "pieces", "per_month", "per_year",
                                "per_session", "per_hour", "one_off", "other"]] = None
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    tag: Literal["verified", "estimated"] = "estimated"
    note: Optional[str] = None
    pitch: Optional[str] = None
    kind: Literal["direct", "substitute"] = "direct"


class Findings(BaseModel):
    competitors: List[Competitor]
    market_note: Optional[str] = None


SYSTEM = """You are the Researcher for a business-idea checker. Search the web to find \
the real competitors for the idea below and what they charge today, in the place the \
person will run the business.

Find 4 to 8 competitors: mostly direct ones (the same kind of product or service for the \
same customer), plus the one or two substitutes customers most often use instead. Include \
any the person named, if you can find them. Prefer the big retailers, marketplaces and \
brand sites people in that country actually buy from.

For each competitor give the current price as shown on a real page you opened or saw in \
search results, with that page's URL, the currency as an ISO code (INR, USD, GBP...), and \
the pack size or billing basis so prices can be compared fairly. Tag a price "verified" \
only when you saw it on that page. If you could only infer it (an old article, a range, a \
different pack), tag it "estimated" and say how you got it in note. If you could not find a \
price at all, leave price empty. Never make up a number, a URL or a brand.

Every competitor, substitutes included, must be a real, named brand and product that a \
customer could buy, never a category such as "regular detergent"; for a substitute, name \
its best-selling brand.

pitch: the main claim the competitor makes, in a few words (for example "plant-based, \
dermatologically tested"). kind: direct or substitute.

If there is little local data, use the closest comparable market, and say which one and \
why in market_note. Otherwise leave market_note empty."""


# ---------- helpers ----------

def idea_text(session: dict) -> str:
    a = session["answers"].get(IDEA_QUESTION) or {}
    text = a.get("value") or ""
    fu = a.get("follow_up") or {}
    if fu.get("answer"):
        text += "\n(%s %s)" % (fu["question"], fu["answer"])
    return text.strip()


def _answer(session: dict, qid: str) -> str:
    a = session["answers"].get(qid) or {}
    if a.get("dont_know"):
        return ""
    value = a.get("value")
    parts = list(value) if isinstance(value, list) else [value]
    if a.get("other"):
        parts.append(a["other"])
    return ", ".join(p for p in parts if p)


_TO_BASE = {"ml": ("l", 0.001), "l": ("l", 1.0), "g": ("kg", 0.001), "kg": ("kg", 1.0), "pieces": ("piece", 1.0)}
_BASIS_LABEL = {"per_month": "month", "per_year": "year", "per_session": "session", "per_hour": "hour"}


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _clean(found: Findings) -> List[dict]:
    """Keep only well-formed findings; work out a comparable unit price."""
    out, seen = [], set()
    for c in found.competitors:
        brand, product = c.brand.strip(), c.product.strip()
        key = re.sub(r"\W+", " ", (brand + " " + product).lower()).strip()
        if not brand or key in seen:
            continue
        seen.add(key)
        url = (c.source_url or "").strip()
        has_source = url.startswith(("http://", "https://"))
        price = c.price if c.price and c.price > 0 else None
        if price is not None and not has_source:
            price = None          # a number with no source is not allowed
        tag = c.tag if price is not None else None
        unit_price = unit = None
        if price is not None and c.pack_unit in _TO_BASE and c.pack_size and c.pack_size > 0:
            unit, factor = _TO_BASE[c.pack_unit]
            unit_price = round(price / (c.pack_size * factor), 2)
        elif price is not None and c.pack_unit in _BASIS_LABEL:
            unit, unit_price = _BASIS_LABEL[c.pack_unit], price
        out.append({
            "brand": brand,
            "product": product,
            "kind": c.kind,
            "pitch": (c.pitch or "").strip() or None,
            "price": price,
            "currency": (c.currency or "").strip().upper() or None,
            "pack_size": c.pack_size,
            "pack_unit": c.pack_unit,
            "unit_price": unit_price,
            "unit": unit,
            "tag": tag,
            "note": (c.note or "").strip() or None,
            "source_url": url if has_source else None,
            "source_name": (c.source_name or "").strip() or (_domain(url) if has_source else None),
        })
    return out


def price_range(competitors: List[dict]) -> Optional[dict]:
    """Deterministic price range over direct competitors with a sourced price."""
    priced = [c for c in competitors if c["price"] is not None and c["kind"] == "direct"]
    if not priced:
        priced = [c for c in competitors if c["price"] is not None]
    if not priced:
        return None
    currencies = {c["currency"] for c in priced}
    currency = max(currencies, key=lambda cur: sum(c["currency"] == cur for c in priced))
    priced = [c for c in priced if c["currency"] == currency]

    result = {
        "currency": currency,
        "pack_low": min(c["price"] for c in priced),
        "pack_high": max(c["price"] for c in priced),
        "count": len(priced),
        "tag": "verified" if all(c["tag"] == "verified" for c in priced) else "estimated",
        "sources": sorted({c["source_name"] for c in priced if c["source_name"]}),
    }
    units = [c["unit"] for c in priced if c["unit"]]
    if units:
        unit = max(set(units), key=units.count)
        per = [c["unit_price"] for c in priced if c["unit"] == unit]
        if len(per) >= 2:
            result.update(unit=unit, unit_low=min(per), unit_high=max(per), unit_count=len(per))
    return result


def _label(c: dict) -> str:
    """"Brand Product", without repeating the brand when the product name has it."""
    if c["product"].lower().startswith(c["brand"].lower()):
        return c["product"]
    return "%s %s" % (c["brand"], c["product"])


def to_facts(competitors: List[dict], rng: Optional[dict]) -> List[dict]:
    today = date.today().isoformat()
    facts = []
    for c in competitors:
        if c["pitch"] and c["source_url"]:
            facts.append({
                "job": JOB,
                "topic": "competitor_claim",
                "claim": "%s is sold as: %s" % (_label(c), c["pitch"]),
                "value": None,
                "unit": None,
                "tag": "verified",
                "reasoning": None,
                "source_url": c["source_url"],
                "source_name": c["source_name"],
                "found_on": today,
            })
        if c["price"] is None:
            continue
        size = ""
        if c["pack_size"] and c["pack_unit"] in _TO_BASE:
            size = " (%g %s)" % (c["pack_size"], c["pack_unit"])
        elif c["pack_unit"] in _BASIS_LABEL:
            size = " per " + _BASIS_LABEL[c["pack_unit"]]
        facts.append({
            "job": JOB,
            "topic": "competitor_price",
            "claim": "%s%s costs %s %g" % (_label(c), size, c["currency"] or "", c["price"]),
            "value": c["price"],
            "unit": c["currency"],
            "tag": c["tag"],
            "reasoning": c["note"],
            "source_url": c["source_url"],
            "source_name": c["source_name"],
            "found_on": today,
        })
    if rng:
        claim = "Direct competitors charge %s %g to %g per pack" % (rng["currency"], rng["pack_low"], rng["pack_high"])
        if rng.get("unit"):
            claim += " (%g to %g per %s)" % (rng["unit_low"], rng["unit_high"], rng["unit"])
        facts.append({
            "job": JOB,
            "topic": "price_range",
            "claim": claim,
            "value": [rng["pack_low"], rng["pack_high"]],
            "unit": rng["currency"],
            "tag": rng["tag"],
            "reasoning": "Worked out from %d sourced competitor prices." % rng["count"],
            "source_url": None,
            "source_name": ", ".join(rng["sources"]),
            "found_on": today,
        })
    return _number(facts, "C")


def _number(facts: List[dict], prefix: str) -> List[dict]:
    """Give each fact a short id the Checker can cite, e.g. C3 or M5."""
    for i, f in enumerate(facts, 1):
        f["id"] = "%s%d" % (prefix, i)
    return facts


# ---------- the research jobs ----------

def _context(session: dict) -> List[str]:
    lines = ["The idea: " + idea_text(session)]
    for qid, label in (("q2", "Where it will run"), ("q14", "Competitors the person named"),
                       ("q10", "Who buys it")):
        value = _answer(session, qid)
        if value:
            lines.append("%s: %s" % (label, value))
    if session.get("business_type"):
        lines.append("Business type: " + session["business_type"].replace("_", " "))
    return lines


def run(session: dict) -> Optional[dict]:
    """Research competitors and prices. Logs usage on `session`; returns the results or None."""
    if not idea_text(session):
        return None
    found = ai.call(session, "research:" + JOB, SYSTEM, "\n".join(_context(session)), Findings,
                    max_tokens=16000, timeout=420.0, effort="medium", web=True)
    if found is None:
        return None
    competitors = _clean(found)
    rng = price_range(competitors)
    return {
        "competitors": competitors,
        "price_range": rng,
        "market_note": (found.market_note or "").strip() or None,
        "facts": to_facts(competitors, rng),
    }


# ---------- market, demand, costs, rules ----------

MARKET_JOB = "market"

MARKET_TOPICS = ("market_size", "market_direction", "demand_signal", "unit_cost", "selling_cost",
                 "customer_cost", "repeat_and_lifetime", "regulation")


class MarketFact(BaseModel):
    topic: Literal["market_size", "market_direction", "demand_signal", "unit_cost", "selling_cost",
                   "customer_cost", "repeat_and_lifetime", "regulation"]
    claim: str
    value: Optional[float] = None
    unit: Optional[str] = None
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    tag: Literal["verified", "estimated"] = "estimated"
    reasoning: Optional[str] = None


class MarketFindings(BaseModel):
    facts: List[MarketFact]
    market_note: Optional[str] = None


MARKET_SYSTEM = """You are the Researcher for a business-idea checker. Search the web for \
the facts a careful advisor would check before backing the idea below, in the place the \
person will run it. Search for every one of these topics and give one to three facts on \
each; leave a topic out only if you searched and found nothing usable:

market_size: how many customers there are, or how much is spent in this category.
market_direction: whether the customer group or category is growing or shrinking.
demand_signal: evidence people already spend money or effort on this problem (sales \
volumes, review counts, search interest, waiting lists).
unit_cost: what one unit or one delivery typically costs to make, buy in, or provide.
selling_cost: fees and costs per sale in the main channels (marketplace commission, \
payment, shipping, returns).
customer_cost: what it typically costs to win one customer (ad cost per click or per sale).
repeat_and_lifetime: how often customers buy again, and how long they stay a customer, \
including any natural point where they stop needing it (for example outgrowing it, \
finishing a course, or a one-time need).
regulation: licences, standards, labelling or advertising-claim rules that apply.

Each fact is one plain sentence in claim, with the number, if any, in value and its unit \
in unit (for example INR, %, months, INR crore). Give the source page URL for every fact. \
Tag a fact "verified" only when you read it on that page. If you had to work it out or \
take it from a nearby market, tag it "estimated" and show how in reasoning. Never make up \
a number, a URL or a source; leave a topic out rather than guess without a basis.

If there is little local data, use the closest comparable market, and say which one and \
why in market_note. Otherwise leave market_note empty."""


def _clean_market(found: MarketFindings) -> List[dict]:
    today = date.today().isoformat()
    facts, seen = [], set()
    for f in found.facts:
        claim = f.claim.strip()
        url = (f.source_url or "").strip()
        has_source = url.startswith(("http://", "https://"))
        reasoning = (f.reasoning or "").strip() or None
        tag = f.tag
        if not has_source:
            if not reasoning:
                continue          # no source and no reasoning shown: not allowed
            tag = "estimated"
        if not claim or claim.lower() in seen:
            continue
        seen.add(claim.lower())
        facts.append({
            "job": MARKET_JOB,
            "topic": f.topic,
            "claim": claim,
            "value": f.value,
            "unit": (f.unit or "").strip() or None,
            "tag": tag,
            "reasoning": reasoning,
            "source_url": url if has_source else None,
            "source_name": (f.source_name or "").strip() or (_domain(url) if has_source else None),
            "found_on": today,
        })
    return _number(facts, "M")


def run_market(session: dict) -> Optional[dict]:
    """Research the market, costs and rules. Logs usage on `session`; returns the results or None."""
    if not idea_text(session):
        return None
    found = ai.call(session, "research:" + MARKET_JOB, MARKET_SYSTEM, "\n".join(_context(session)),
                    MarketFindings, max_tokens=16000, timeout=420.0, effort="medium", web=True)
    if found is None:
        return None
    return {"market_note": (found.market_note or "").strip() or None, "facts": _clean_market(found)}
