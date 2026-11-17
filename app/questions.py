"""Fixed question bank (brief section 4). Step 1 covers the one-idea path only.

Question types:
  choice - pick one option
  multi  - pick any options, plus optional free text
  text   - one short line
  long   - a few sentences
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

QUESTIONS = [
    # Stage 0: Entry
    {
        "id": "q1", "stage": 0, "type": "choice", "allow_unknown": False,
        "text": "Where are you right now?",
        "options": [
            {"label": ONE_IDEA},
            {"label": "I have a few ideas", "soon": True},
            {"label": "I have no idea yet", "soon": True},
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
    # Stage 2: The idea (one-idea path)
    {
        "id": "q8", "stage": 2, "type": "long",
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
