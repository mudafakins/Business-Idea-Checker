# Business Idea Checker

A local web app that checks a business idea the way a careful advisor would. It asks one
question at a time (or takes everything in one go in quick mode), researches the real
market, competitors, prices and costs on the web while you answer, flags answers that
conflict with the data, and ends with a short, plain-language report:

- a verdict: **Pursue**, **Test small first**, or **Drop or rethink**, with a confidence level
- six rated areas (Demand, Market, Competition, Money, Fit, Risk), each with its evidence
- a simplified business plan, charts, every alert, and the cheapest next test with green
  gates and a kill switch
- every number tagged **verified**, **estimated** or **assumed**, with sources

It works for one idea, a few ideas (it ranks them), or no idea yet (it suggests some).

## How the verdict stays honest

The AI gathers evidence and writes the explanation, but the verdict is decided by fixed
rules in plain code (`app/scorer.py`). Research for the same idea is reused for 7 days and
assessments are cached by the exact answers and findings, so the same inputs always give
the same verdict. Each agent works from an expert playbook (`app/knowledge.py`).

## Run it

Needs Python 3.9 or later and macOS or Linux.

```bash
./run.sh
```

The first run sets everything up, then opens http://127.0.0.1:8765.

### AI access

Either:

- **Claude subscription** (default): install [Claude Code](https://claude.com/claude-code),
  run `claude` in a terminal and type `/login`. The app calls the `claude` command in the
  background. Local, single-user use only.
- **Claude API key**: copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.

Without either, the form still works with standard wording, but there is no research or report.

## Setting it up on a team member's computer

1. `git clone https://github.com/mudafakins/Business-Idea-Checker.git`
2. Install [Claude Code](https://claude.com/claude-code), run `claude`, type `/login` and
   sign in with your own Claude account (the AI calls use your own plan).
3. In the folder: `./run.sh`

Use `git clone` (not a ZIP download) so automatic updates work.

## Updates

The app keeps itself up to date with this repository:

- `./run.sh` pulls the latest pushed version each time it starts.
- While it is open, it checks every 30 minutes and shows **Update now** when a newer
  version has been pushed. Updating installs any new packages and restarts the app.
- It never overwrites local edits to the app's files, and waits if a check is running.
  Saved reports and `.env` are never touched.

To release an update, commit and `git push` (tag it, e.g. `git tag v1.4`, to give it a name).

## Where things are saved

Every run is saved in `reports/` as JSON, plus a standalone HTML copy of each finished
report. The History screen reopens them. Reports can be saved as PDF from the browser.

## Test plan

With the app running:

```bash
.venv/bin/python tests/test_plan.py
```

Runs a reference case twice plus likely-good and likely-bad ideas end to end and prints
each verdict.

## Layout

| Path | What it does |
|---|---|
| `app/main.py` | Web server, background jobs, report pipeline |
| `app/questions.py` | The fixed question bank |
| `app/conductor.py` | Rewords and skips questions, asks one follow-up |
| `app/researcher.py` | Web research and the fact store |
| `app/checker.py` | Alerts when answers conflict with the data |
| `app/scorer.py` | Evidence gathering and the fixed verdict rules |
| `app/writer.py` | The plain-language report text |
| `app/ideas.py`, `app/quick.py` | Few-ideas / no-idea paths, quick mode |
| `app/knowledge.py` | Expert playbooks for the agents |
| `static/` | The form, report page and charts |

The report is guidance, not financial or legal advice.
