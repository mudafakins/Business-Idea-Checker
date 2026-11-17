"""Fixed question bank (brief section 4), for all three entry paths.

Question types:
  choice - pick one option
  multi  - pick any options, plus optional free text
  text   - one short line
  long   - a few sentences
  pick   - choose one idea from a list the agent makes (few-ideas and no-idea paths)

A question with "paths" is only asked on those paths; the rest are asked on all.
On the few-ideas and no-idea paths, the idea picked becomes the answer to q8.
"""

STAGES = [
    {"id": 0, "name": "Start"},
    {"id": 1, "name": "About you"},
    {"id": 2, "name": "The idea"},
    {"id": 3, "name": "Customer"},
    {"id": 4, "name": "Market"},
    {"id": 5, "name": "Money"},
    {"id": 6, "name": "Report"},
]

ONE_IDEA = "I have one idea"
FEW_IDEAS = "I have a few ideas"
NO_IDEA = "I have no idea yet"
PATHS = {ONE_IDEA: "one", FEW_IDEAS: "few", NO_IDEA: "none"}

QUESTIONS = [
    # Stage 0: Entry
    {
        "id": "q1", "stage": 0, "type": "choice", "allow_unknown": False,
        "text": "Where are you right now?",
        "options": [
            {"label": ONE_IDEA},
            {"label": FEW_IDEAS},
            {"label": NO_IDEA},
        ],
    },
    # Stage 1: About you and your non-negotiables
    {
        "id": "q2", "stage": 1, "type": "text",
        "text": "Which country and city will you run this from?",
        "placeholder": "e.g. India, Mumbai",
    },
    {
        "id": "q3", "stage": 1, "type": "text",
        "text": "How much money can you put in without hurting yourself if it's lost?",
        "placeholder": "Amount and currency",
    },
    {
        "id": "q4", "stage": 1, "type": "text",
        "text": "How much time can you give it each week?",
        "placeholder": "e.g. 10 hours, or full time",
    },
    {
        "id": "q5", "stage": 1, "type": "long",
        "text": "What are you good at, or have access to, that most people don't?",
        "hint": "Skills, contacts, an audience, a location.",
    },
    {
        "id": "q6", "stage": 1, "type": "choice",
        "text": "When do you need it to start earning?",
        "options": [
            {"label": "From day 1"},
            {"label": "Within 6 months"},
            {"label": "1 to 2 years"},
            {"label": "I can wait"},
        ],
    },
    {
        "id": "q7", "stage": 1, "type": "multi",
        "text": "What will you refuse to do?",
        "hint": "Pick any that apply, and add your own.",
        "options": [
            {"label": "No inventory"},
            {"label": "No employees"},
            {"label": "No selling in person"},
            {"label": "No borrowing"},
            {"label": "No building new technology"},
        ],
        "other_placeholder": "Anything else?",
    },
    # Stage 2: The idea
    {
        "id": "q8_list", "stage": 2, "type": "long", "paths": ["few"], "allow_unknown": False,
        "text": "List your ideas, one per line.",
        "hint": "A sentence each is enough. We will check each one against what you told us.",
        "placeholder": "Idea one\nIdea two\nIdea three",
    },
    {
        "id": "q8_problems", "stage": 2, "type": "long", "paths": ["none"],
        "text": "What problems annoy you or people around you?",
        "hint": "Everyday hassles, things that cost too much, things nobody does well.",
    },
    {
        "id": "q9", "stage": 2, "type": "long", "paths": ["none"],
        "text": "Which industries or customers do you know well?",
    },
    {
        "id": "pick", "stage": 2, "type": "pick", "paths": ["few", "none"], "allow_unknown": False,
        "text": "Which idea do you want to test?",
    },
    {
        "id": "q8", "stage": 2, "type": "long", "paths": ["one"],
        "text": "Describe your idea in one or two sentences.",
    },
    # Stage 3: Customer and problem
    {
        "id": "q10", "stage": 3, "type": "long",
        "text": "Who exactly buys this?",
        "hint": "Describe one real person if you can.",
    },
    {
        "id": "q11", "stage": 3, "type": "long",
        "text": "What problem does it solve for them, and what do they do about it today?",
    },
    {
        "id": "q12", "stage": 3, "type": "long",
        "text": "Why would they switch to you?",
    },
    {
        "id": "q13", "stage": 3, "type": "choice",
        "text": "Have you spoken to anyone who would buy it?",
        "options": [
            {"label": "No"},
            {"label": "A few people"},
            {"label": "Many"},
            {"label": "I already have paying customers"},
        ],
    },
    # Stage 4: Market and competitors
    {
        "id": "q14", "stage": 4, "type": "long",
        "text": "Who do you think your competitors are?",
    },
    {
        "id": "q15", "stage": 4, "type": "text", "no_skip": True,
        "text": "Which competitor is closest to what you want to be?",
        "research": "competitors",
    },
    {
        "id": "q16", "stage": 4, "type": "long",
        "text": "What will you do that they don't?",
    },
    # Stage 5: Money
    {
        "id": "q17", "stage": 5, "type": "text",
        "text": "What will you charge?",
        "placeholder": "Price and currency",
        "research": "prices",
    },
    {
        "id": "q18", "stage": 5, "type": "multi", "adapt_options": True,
        "text": "How will customers find you and buy?",
        "hint": "Pick any that apply, and add your own.",
        "options": [
            {"label": "Online marketplaces"},
            {"label": "My own website"},
            {"label": "Social media"},
            {"label": "Paid ads"},
            {"label": "Word of mouth and referrals"},
            {"label": "A physical shop or location"},
            {"label": "Direct sales and outreach"},
        ],
        "other_placeholder": "Another way?",
    },
    {
        "id": "q19", "stage": 5, "type": "text",
        "text": "Will a customer buy once or keep coming back? How often?",
        "placeholder": "e.g. once, or every month",
    },
    {
        "id": "q20", "stage": 5, "type": "text",
        "text": "What does it cost you to deliver one sale?",
        "placeholder": "Amount and currency",
    },
]

QUESTIONS_BY_ID = {q["id"]: q for q in QUESTIONS}


def path_of(session: dict) -> str:
    """"one", "few" or "none", from the answer to the first question."""
    a = session.get("answers", {}).get("q1") or {}
    return PATHS.get(a.get("value"), "one")


def visible(question: dict, path: str) -> bool:
    return not question.get("paths") or path in question["paths"]
