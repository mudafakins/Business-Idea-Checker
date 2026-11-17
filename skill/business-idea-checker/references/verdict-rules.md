# Verdict rules

The verdict is computed from fixed rules. Your job is to gather the evidence; `scripts/score.py`
(or these rules by hand) turns it into ratings and a verdict.

## 1. Answer the evidence questions

"yes" is always the good answer. Answer "yes" or "no" only when the answers or findings clearly
support it; otherwise "unclear". For each, give the tag of the evidence behind it (verified,
estimated or assumed) and a one-line note. Definitions are in `playbooks.md`, "Answering the
evidence questions".

| id | area | question |
|---|---|---|
| D1 | demand | People already pay for products or services that solve this problem. |
| D2 | demand | There is evidence of active buying or searching: sales volumes, review counts, search interest. |
| M1 | market | The customer group is big enough to support this business at the planned scale. |
| M2 | market | The customer group or category is stable or growing, not shrinking. |
| C1 | competition | There is a real gap that current competitors do not fill. |
| C2 | competition | The person's stated difference is not already claimed by competitors. |
| C3 | competition | That difference would be hard for competitors to copy. |
| F1 | fit | The idea as described breaks none of the person's non-negotiables. |
| F2 | fit | The time the person can give matches what the first stage needs. |
| F3 | fit | The person's skills, contacts or access match what the business needs. |
| F4 | fit | The business can start earning within the timeline the person needs. |
| R1 | risk | Nothing found makes the business as described unlawful or unsafe to run, or needs a licence or permission it realistically cannot get. |
| R2 | risk | The business does not depend on one platform, supplier or partner that could end it. |

Also record **spoken_to** from question 13: none, few, many or paying.

## 2. The money figures

All in one currency. Each has a value (or null) and a tag.
- **price**: what one sale is charged.
- **cost_per_sale**: every cost that comes with one more sale (making or buying, packing,
  delivery, channel and payment fees, expected returns), not advertising.
- **customer_cost**: the cost to win one NEW paying customer. Not advertising cost of sale
  (ad spend as a share of all sales), which looks far cheaper. If only cost per click is
  known: cost per click divided by conversion rate. Unknown is better than a wrong number.
- **purchases_per_year** and **lifetime_months**: how often one customer buys, and for how long.
- **budget**: what they said they can put in.
- **startup_items**: three to six costs of a small first stage, each with an amount and tag.

## 3. The JSON for scripts/score.py

```json
{
  "currency": "INR",
  "spoken_to": "few",
  "signals": {
    "D1": {"value": "yes", "tag": "verified", "note": "Seven brands sell it at 290-540 per litre"},
    "D2": {"value": "unclear", "tag": "assumed", "note": "No sales volumes found"}
  },
  "money": {
    "price": {"value": 449, "tag": "assumed"},
    "cost_per_sale": {"value": 170, "tag": "assumed"},
    "customer_cost": {"value": null, "tag": null},
    "purchases_per_year": {"value": 12, "tag": "assumed"},
    "lifetime_months": {"value": 18, "tag": "estimated"},
    "budget": {"value": 1000000, "tag": "assumed"}
  },
  "startup_items": [
    {"item": "First stock", "amount": 170000, "tag": "estimated"}
  ]
}
```

Any signal left out counts as "unclear".

## 4. The rules (what the script does)

Area ratings:
- **Demand**: weak if D1 is no. Strong if D1 is yes and they already have paying customers, or
  if D1 and D2 are yes and they have spoken to many buyers. Otherwise okay.
- **Market**: weak if M1 or M2 is no. Strong if both are yes. Otherwise okay.
- **Competition**: weak if C1 and C2 are both no. Strong if C1, C2 and C3 are all yes. Otherwise okay.
- **Money**, from the figures: kept per sale = price − cost per sale; margin = kept ÷ price;
  purchases per customer = purchases per year × lifetime months ÷ 12 (at least 1); customer
  worth = kept × purchases. Weak if kept ≤ 0, margin < 10%, or worth < cost to win them.
  Strong if worth ≥ 3 × cost to win them and margin ≥ 40%. Okay otherwise, including when
  price or cost is unknown.
- **Fit**: weak if F1 is no, or the first stage costs more than the budget. Strong if it fits
  the budget and F2, F3 and F4 are yes. Otherwise okay.
- **Risk**: weak if R1 is no (a blocker). Strong if R1 and R2 are yes. Otherwise okay.

Each area's evidence basis: verified if at least half its tags are verified; estimated if
any are verified or estimated; otherwise assumed.

Verdict, first rule that applies:
1. F1 is no → **Drop or rethink** (breaks a non-negotiable).
2. R1 is no → **Drop or rethink** (blocking risk).
3. Demand or Money weak on verified evidence → **Drop or rethink**.
4. Three or more weak areas → **Drop or rethink**.
5. Two weak areas, at least one on verified evidence → **Drop or rethink** (a cheap test
   cannot settle what verified data already shows).
6. No weak areas and Demand and Money both strong → **Pursue**.
7. Otherwise → **Test small first**.

Cap: if under 30% of the evidence is verified, Pursue becomes Test small first.
Confidence: high if 60% or more is verified, medium from 30%, low below.
