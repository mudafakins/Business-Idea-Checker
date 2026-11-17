"""Quick mode: one block of text instead of the form.

The person writes everything about their idea in their own words. One AI call
sorts it into the form's answers, taking only what the text actually says;
anything not mentioned is stored as "I don't know", which the report treats
like any other unknown (filled with labelled estimates, never invented).

The same call checks the idea stage rules: an illegal or unsafe idea is
declined politely, and the business type is set for the later steps.
"""

from typing import List, Optional

from pydantic import BaseModel
from typing_extensions import Literal

from . import ai
from .questions import ONE_IDEA, QUESTIONS_BY_ID

EARNING = ["From day 1", "Within 6 months", "1 to 2 years", "I can wait"]
SPOKEN = ["No", "A few people", "Many", "I already have paying customers"]
REFUSALS = ["No inventory", "No employees", "No selling in person", "No borrowing", "No building new technology"]


class QuickAnswers(BaseModel):
    decline_reason: Optional[str] = None
    business_type: Literal["product", "service", "software", "local_business", "marketplace"]
    q8_idea: Optional[str] = None
    q2_place: Optional[str] = None
    q3_money: Optional[str] = None
    q4_time: Optional[str] = None
    q5_strengths: Optional[str] = None
    q6_start_earning: Optional[Literal["From day 1", "Within 6 months", "1 to 2 years", "I can wait"]] = None
    q7_refuses: List[str] = []
    q7_refuses_other: Optional[str] = None
    q10_buyer: Optional[str] = None
    q11_problem_today: Optional[str] = None
    q12_why_switch: Optional[str] = None
    q13_spoken_to: Optional[Literal["No", "A few people", "Many", "I already have paying customers"]] = None
    q14_competitors: Optional[str] = None
    q16_difference: Optional[str] = None
    q17_price: Optional[str] = None
    q18_channels: List[str] = []
    q19_repeat: Optional[str] = None
    q20_cost_per_sale: Optional[str] = None


SYSTEM = """You sort a person's free-form description of a business idea into the answers \
of a fixed form. Take only what the text actually says. Use the person's own words and \
numbers, lightly tidied. If the text does not say something, leave that field empty: never \
guess, infer a number, or fill a gap with what is typical.

Fields:
- q8_idea: the idea in one or two sentences: what it is, who it is for, and how it is sold, \
as far as the text says. If several ideas are described, use the main one.
- q2_place: country and city. q3_money: money they can put in. q4_time: time per week.
- q5_strengths: skills, contacts, audience or location that give them an edge.
- q6_start_earning: only if the text makes it clear, one of: %s.
- q7_refuses: any of these the text clearly says they will not do: %s. Put every other limit \
or rule they set themselves (for example "no R&D", "one product to start", "online only") in \
q7_refuses_other, in their own words.
- q10_buyer: who exactly buys. q11_problem_today: the problem and what buyers do about it today.
- q12_why_switch: why buyers would switch to them.
- q13_spoken_to: only if stated, one of: %s.
- q14_competitors: competitors they name. q16_difference: what they will do that competitors don't.
- q17_price: what they will charge, with the unit or pack size if given.
- q18_channels: how customers will find them and buy, as short phrases.
- q19_repeat: whether customers buy once or come back, and how often.
- q20_cost_per_sale: what one sale costs them to deliver.

business_type: product, service, software, local_business or marketplace.

Only if the idea is clearly illegal where the person lives, or could seriously harm people, set \
decline_reason to one polite sentence saying why it cannot be checked. Ordinary risky, regulated \
or unusual businesses are not declined.""" % (", ".join(EARNING), ", ".join(REFUSALS), ", ".join(SPOKEN))

# Field -> question id, in form order.
FIELDS = [("q2_place", "q2"), ("q3_money", "q3"), ("q4_time", "q4"), ("q5_strengths", "q5"),
          ("q6_start_earning", "q6"), (None, "q7"), ("q8_idea", "q8"), ("q10_buyer", "q10"),
          ("q11_problem_today", "q11"), ("q12_why_switch", "q12"), ("q13_spoken_to", "q13"),
          ("q14_competitors", "q14"), (None, "q15"), ("q16_difference", "q16"), ("q17_price", "q17"),
          (None, "q18"), ("q19_repeat", "q19"), ("q20_cost_per_sale", "q20")]


def extract(session: dict, text: str) -> Optional[QuickAnswers]:
    return ai.call(session, "quick", SYSTEM, "THE PERSON'S DESCRIPTION\n" + text.strip(), QuickAnswers,
                   max_tokens=8000, timeout=120.0, effort="low")


def to_answers(found: QuickAnswers, stamp: str) -> dict:
    """The form's answers. Anything the text did not cover is "I don't know"."""
    def entry(qid, value, other=None):
        known = bool(value) or bool(other)
        return {"question": QUESTIONS_BY_ID[qid]["text"], "value": value if known else None,
                "other": other, "dont_know": not known, "answered_at": stamp, "from_quick": True}

    answers = {"q1": entry("q1", ONE_IDEA)}
    for field, qid in FIELDS:
        if qid == "q7":
            picked = [r for r in found.q7_refuses if r in REFUSALS]
            other = (found.q7_refuses_other or "").strip() or None
            answers[qid] = entry(qid, picked, other)
        elif qid == "q18":
            answers[qid] = entry(qid, [c.strip() for c in found.q18_channels if c.strip()])
        elif field is None:
            answers[qid] = entry(qid, None)            # q15 needs the research: left open
        else:
            value = getattr(found, field)
            answers[qid] = entry(qid, value.strip() if isinstance(value, str) and value.strip() else None)
    return answers
