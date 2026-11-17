"""Business Idea Checker - local server: the form, Conductor, Researcher, Checker, Scorer and Writer."""

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from . import checker, conductor, ideas, researcher, scorer, writer  # noqa: E402  (need the environment loaded first)
from .questions import QUESTIONS, QUESTIONS_BY_ID, STAGES, path_of  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
STATIC = ROOT / "static"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

SESSION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

app = FastAPI(title="Business Idea Checker")


class Answer(BaseModel):
    value: Optional[Union[str, List[str]]] = None
    other: Optional[str] = None
    dont_know: bool = False


class FollowUpAnswer(BaseModel):
    value: Optional[str] = None


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def session_path(session_id: str) -> Path:
    if not SESSION_ID.match(session_id):
        raise HTTPException(404, "Session not found")
    return REPORTS / f"{session_id}.json"


def load(session_id: str) -> dict:
    path = session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save(session: dict) -> None:
    session["updated_at"] = now()
    path = session_path(session["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@app.get("/api/questions")
def get_questions():
    return {"stages": STAGES, "questions": QUESTIONS, "ai": conductor.enabled()}


@app.post("/api/sessions")
def create_session():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session = {
        "id": f"{stamp}-{secrets.token_hex(2)}",
        "created_at": now(),
        "updated_at": now(),
        "path": "one_idea",
        "completed": False,
        "answers": {},
    }
    save(session)
    return session


# ---------- history: every saved run in reports/ ----------

def _idea_title(session: dict) -> Optional[str]:
    answers = session.get("answers") or {}
    pick = answers.get("pick") or {}
    if pick.get("value"):
        return pick["value"]
    idea = (answers.get("q8") or {}).get("value")
    if idea:
        return idea if len(idea) <= 90 else idea[:87].rsplit(" ", 1)[0] + "…"
    return None


def _summary(session: dict) -> dict:
    report = session.get("report") or {}
    scores = report.get("scores") or {}
    asked = [q for q in QUESTIONS if not q.get("paths") or path_of(session) in q["paths"]]
    answered = sum(1 for q in asked if q["id"] in session.get("answers", {}))
    usage = session.get("usage") or {}
    return {
        "id": session["id"],
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "title": _idea_title(session),
        "path": path_of(session),
        "completed": bool(session.get("completed")),
        "declined": bool(session.get("declined")),
        "answered": answered,
        "questions": len(asked),
        "verdict": scores.get("verdict") if report.get("status") == "done" else None,
        "confidence": scores.get("confidence") if report.get("status") == "done" else None,
        "report_at": report.get("finished_at") if report.get("status") == "done" else None,
        "has_file": (REPORTS / ("%s.html" % session["id"])).exists(),
        "usage": {k: usage.get(k) for k in ("calls", "searches", "cost_usd")},
    }


@app.get("/api/sessions")
def list_sessions():
    """Every saved run, newest first, for the History screen."""
    out = []
    for path in REPORTS.glob("*.json"):
        if not SESSION_ID.match(path.stem):
            continue
        try:
            out.append(_summary(json.loads(path.read_text(encoding="utf-8"))))
        except (ValueError, KeyError, OSError):
            continue          # a damaged file is skipped, not fatal
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


class ReportFile(BaseModel):
    html: str


@app.post("/api/sessions/{session_id}/report/file")
def save_report_file(session_id: str, body: ReportFile):
    """Saves the rendered report as a standalone HTML file next to its JSON."""
    session = load(session_id)
    if (session.get("report") or {}).get("status") != "done":
        raise HTTPException(409, "No finished report to save")
    if len(body.html) > 3_000_000:
        raise HTTPException(413, "Report too large")
    path = REPORTS / ("%s.html" % session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body.html, encoding="utf-8")
    os.replace(tmp, path)
    return {"file": "reports/%s.html" % session_id}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = load(session_id)
    for job, (key, _) in JOBS.items():
        if session.get(key):
            session[key] = job_status(session, job)
    return session


def shown_text(session: dict, question: dict) -> str:
    """The question as the person saw it (reworded by the Conductor, if it was)."""
    return session.get("questions", {}).get(question["id"], {}).get("text", question["text"])


# ---------- running the Conductor without blocking the form ----------
#
# A Conductor call takes several seconds. It works on a snapshot of the session
# and its results are merged into the latest saved copy afterwards, so answers
# given in the meantime are never overwritten.

_locks = defaultdict(threading.Lock)   # one per session, guards the file
_jobs = defaultdict(list)              # background checks still running


def run_conductor(session_id: str, question_id: str, work) -> dict:
    """work(snapshot) -> follow-up text or None. Returns the merged session."""
    with _locks[session_id]:
        snapshot = load(session_id)
    answered_at = snapshot["answers"][question_id].get("answered_at")
    calls_before = len(snapshot.get("usage", {}).get("log", []))
    follow_up = work(snapshot)

    with _locks[session_id]:
        session = load(session_id)
        for key in ("business_type", "questions"):
            if key in snapshot:
                session[key] = snapshot[key]
        if "declined" in snapshot:
            session["declined"] = snapshot["declined"]
        else:
            session.pop("declined", None)
        # Skips: only for questions the person has not reached themselves.
        for qid, a in snapshot["answers"].items():
            if a.get("inferred") and qid not in session["answers"]:
                session["answers"][qid] = a
        # Follow-up: only if the answer it is about has not changed since.
        current = session["answers"].get(question_id)
        if follow_up and current and current.get("answered_at") == answered_at and not current.get("follow_up"):
            current["follow_up"] = {"question": follow_up, "answer": None, "status": "pending"}
        merge_usage(session, snapshot, calls_before)
        save(session)
        return session


def merge_usage(session: dict, snapshot: dict, calls_before: int) -> None:
    """Copy the AI calls logged on a snapshot into the latest saved session."""
    new_calls = snapshot.get("usage", {}).get("log", [])[calls_before:]
    if not new_calls:
        return
    usage = session.setdefault("usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "searches": 0, "log": []})
    for call in new_calls:
        usage["calls"] += 1
        usage["input_tokens"] += call["input_tokens"]
        usage["output_tokens"] += call["output_tokens"]
        usage["searches"] = usage.get("searches", 0) + call.get("searches", 0)
        if call.get("cost_usd") is not None:
            usage["cost_usd"] = round(usage.get("cost_usd", 0) + call["cost_usd"], 4)
        usage["log"].append(call)


# ---------- background research ----------
#
# Two jobs run side by side once the idea is described. Each job's state lives
# in its own key of the session; findings go into session["facts"].

JOBS = {
    researcher.JOB: ("research", researcher.run),               # competitors and prices
    researcher.MARKET_JOB: ("market_research", researcher.run_market),
}
_research = {}   # (session id, job) -> the research thread running in this server


def _job_alive(session_id: str, job: str) -> bool:
    thread = _research.get((session_id, job))
    return bool(thread and thread.is_alive())


def start_research(session_id: str, retry_failed: bool = False) -> None:
    """Start each research job unless it is already running or done for this idea."""
    with _locks[session_id]:
        session = load(session_id)
        idea = researcher.idea_text(session)
        if not idea or not conductor.enabled():
            return
        to_start = []
        for job, (key, _) in JOBS.items():
            current = session.get(key) or {}
            alive = _job_alive(session_id, job)
            if current.get("idea") == idea:
                if current.get("status") == "done" or alive:
                    continue
                if current.get("status") == "failed" and not retry_failed:
                    continue
            session[key] = {"status": "running", "idea": idea, "started_at": now()}
            to_start.append(job)
        if not to_start:
            return
        save(session)
        snapshot = json.loads(json.dumps(session))

    for job in to_start:
        thread = threading.Thread(target=_research_job, args=(session_id, job, idea, snapshot), daemon=True)
        _research[(session_id, job)] = thread
        thread.start()


def _research_job(session_id: str, job: str, idea: str, snapshot: dict) -> None:
    key, run = JOBS[job]
    snapshot = json.loads(json.dumps(snapshot))
    calls_before = len(snapshot.get("usage", {}).get("log", []))
    try:
        result = run(snapshot)
    except Exception:
        logging.getLogger("researcher").exception("research job %s failed", job)
        result = None
    with _locks[session_id]:
        session = load(session_id)
        merge_usage(session, snapshot, calls_before)
        state = session.get(key) or {}
        if state.get("idea") == idea:   # the idea has not been changed since
            state["finished_at"] = now()
            if result is None:
                state["status"] = "failed"
            else:
                state["status"] = "done"
                state["market_note"] = result.get("market_note")
                if job == researcher.JOB:
                    state.update(competitors=result["competitors"], price_range=result["price_range"])
                others = [f for f in session.get("facts", []) if f.get("job") != job]
                session["facts"] = others + result["facts"]
            session[key] = state
        save(session)
    if result is not None:
        schedule_check(session_id)


def job_status(session: dict, job: str) -> dict:
    key = JOBS[job][0]
    state = session.get(key) or {"status": "none"}
    # Research that was running when the app was restarted will never finish.
    if state.get("status") == "running" and not _job_alive(session["id"], job):
        state = dict(state, status="failed")
    return state


def research_status(session: dict) -> dict:
    """The competitor research, as the form shows it on questions 15 and 17."""
    return job_status(session, researcher.JOB)


@app.get("/api/sessions/{session_id}/research")
def get_research(session_id: str):
    return research_status(load(session_id))


@app.post("/api/sessions/{session_id}/research")
def retry_research(session_id: str):
    session = load(session_id)
    with _locks[session_id]:
        for job, (key, _) in JOBS.items():
            if job_status(session, job)["status"] == "failed" and session.get(key):
                session[key]["status"] = "failed"
        save(session)
    start_research(session_id, retry_failed=True)
    return research_status(load(session_id))


# ---------- the Checker ----------
#
# Runs in the background after answers and when research finishes. Only one
# check runs per session at a time; a request that arrives meanwhile makes it
# run once more at the end, so the alerts always reflect the latest answers.

_checks = {}                      # session id -> {"thread": ..., "again": bool}
_checks_lock = threading.Lock()


def schedule_check(session_id: str) -> None:
    if not conductor.enabled():
        return
    with _checks_lock:
        state = _checks.get(session_id)
        if state and state["thread"].is_alive():
            state["again"] = True
            return
        state = {"again": False}
        thread = threading.Thread(target=_check_loop, args=(session_id, state), daemon=True)
        state["thread"] = thread
        _checks[session_id] = state
        _jobs[session_id].append(thread)
        thread.start()


def _check_loop(session_id: str, state: dict) -> None:
    while True:
        try:
            _check_once(session_id)
        except Exception:
            logging.getLogger("checker").exception("check failed")
        with _checks_lock:
            if not state["again"]:
                return
            state["again"] = False


def _check_once(session_id: str) -> None:
    with _locks[session_id]:
        snapshot = load(session_id)
    if not researcher.idea_text(snapshot):
        return
    calls_before = len(snapshot.get("usage", {}).get("log", []))
    alerts = checker.run(snapshot)
    with _locks[session_id]:
        session = load(session_id)
        merge_usage(session, snapshot, calls_before)
        if alerts is not None:
            session["alerts"] = checker.merge(session.get("alerts") or [], alerts)
        save(session)


def checking(session_id: str) -> bool:
    state = _checks.get(session_id)
    return bool(state and state["thread"].is_alive())


class SeenAlerts(BaseModel):
    keys: List[str]


@app.get("/api/sessions/{session_id}/alerts")
def get_alerts(session_id: str):
    session = load(session_id)
    return {"alerts": session.get("alerts") or [], "checking": checking(session_id)}


@app.post("/api/sessions/{session_id}/alerts/seen")
def mark_alerts_seen(session_id: str, body: SeenAlerts):
    with _locks[session_id]:
        session = load(session_id)
        for a in session.get("alerts") or []:
            if a["key"] in body.keys:
                a["seen"] = True
        save(session)
    return {"alerts": session.get("alerts") or [], "checking": checking(session_id)}


def run_in_background(session_id: str, question_id: str, work) -> None:
    def job():
        try:
            run_conductor(session_id, question_id, work)
        except Exception:
            logging.getLogger("conductor").exception("background check failed")
    thread = threading.Thread(target=job, daemon=True)
    _jobs[session_id].append(thread)
    thread.start()


def wait_for_background(session_id: str, seconds: float) -> None:
    deadline = time.time() + seconds
    for thread in _jobs[session_id]:
        thread.join(max(0.0, deadline - time.time()))
    _jobs[session_id] = [t for t in _jobs[session_id] if t.is_alive()]


def has_pending_follow_up(session: dict) -> bool:
    return any((a.get("follow_up") or {}).get("status") == "pending" for a in session["answers"].values())


@app.put("/api/sessions/{session_id}/answers/{question_id}")
def put_answer(session_id: str, question_id: str, answer: Answer):
    question = QUESTIONS_BY_ID.get(question_id)
    if question is None:
        raise HTTPException(404, "Unknown question")
    with _locks[session_id]:
        session = load(session_id)
        session["answers"][question_id] = {
            "question": shown_text(session, question),
            "value": answer.value,
            "other": answer.other,
            "dont_know": answer.dont_know,
            "answered_at": now(),
        }
        save(session)

    if conductor.enabled() and question_id in IDEA_INPUTS:
        # The few-ideas and no-idea paths: start working out the shortlist now.
        if question_id in ("q8_list", "q9"):
            start_ideas(session_id)
        return {"session": load(session_id)}

    if conductor.enabled() and not answer.dont_know:
        if question_id == conductor.IDEA_QUESTION:
            # The next questions depend on this, so the form waits for it.
            session = run_conductor(session_id, question_id,
                                    lambda snap: conductor.plan(snap, allow_follow_up=True))
            if session.get("declined"):
                return {"session": session}
            if not (session["answers"][question_id].get("follow_up") or {}).get("status") == "pending":
                start_research(session_id)
        elif question["type"] in ("text", "long"):
            # Vagueness check and skips: the form does not wait.
            run_in_background(session_id, question_id,
                              lambda snap: conductor.review(snap, question_id))
    if researcher.idea_text(session):
        schedule_check(session_id)
    return {"session": session}


# ---------- few ideas and no idea: the shortlist ----------

IDEA_INPUTS = {"q8_list", "q8_problems", "q9"}
_idea_jobs = {}   # session id -> thread


def _ideas_basis(session: dict) -> str:
    """What the shortlist was made from; when it changes, the shortlist is redone."""
    keys = ["q2", "q3", "q4", "q5", "q6", "q7"] + (["q8_list"] if path_of(session) == "few" else ["q8_problems", "q9"])
    return json.dumps([[k, (session["answers"].get(k) or {}).get("value"), (session["answers"].get(k) or {}).get("other")]
                       for k in keys], ensure_ascii=False)


def start_ideas(session_id: str, more: bool = False) -> None:
    with _locks[session_id]:
        session = load(session_id)
        path = path_of(session)
        if path == "one" or not conductor.enabled():
            return
        basis = _ideas_basis(session)
        current = session.get("ideas") or {}
        alive = bool(_idea_jobs.get(session_id) and _idea_jobs[session_id].is_alive())
        if alive:
            return
        same = current.get("basis") == basis
        if same and current.get("status") == "done" and not more:
            return
        keep = current.get("items", []) if same and more and path == "none" else []
        session["ideas"] = {"status": "running", "mode": "rank" if path == "few" else "suggest",
                            "basis": basis, "items": keep, "started_at": now()}
        save(session)
        snapshot = json.loads(json.dumps(session))

    def job():
        calls_before = len(snapshot.get("usage", {}).get("log", []))
        try:
            if path == "few":
                result = ideas.rank(snapshot)
            else:
                fresh = ideas.suggest(snapshot, keep)
                result = None if fresh is None else ideas.order(keep + fresh)
        except Exception:
            logging.getLogger("ideas").exception("idea job failed")
            result = None
        with _locks[session_id]:
            session = load(session_id)
            merge_usage(session, snapshot, calls_before)
            state = session.get("ideas") or {}
            if state.get("basis") == basis:
                state["finished_at"] = now()
                if result is None:
                    state["status"] = "failed"
                else:
                    state.update(status="done", items=result)
                session["ideas"] = state
            save(session)

    thread = threading.Thread(target=job, daemon=True)
    _idea_jobs[session_id] = thread
    thread.start()


def ideas_status(session: dict) -> dict:
    state = dict(session.get("ideas") or {"status": "none", "items": []})
    thread = _idea_jobs.get(session["id"])
    if state.get("status") == "running" and not (thread and thread.is_alive()):
        state["status"] = "failed"
    if state.get("status") == "done" and state.get("basis") != _ideas_basis(session):
        state["stale"] = True
    state.pop("basis", None)
    return state


class IdeasRequest(BaseModel):
    more: bool = False


class PickRequest(BaseModel):
    idea_id: str


@app.get("/api/sessions/{session_id}/ideas")
def get_ideas(session_id: str):
    return ideas_status(load(session_id))


@app.post("/api/sessions/{session_id}/ideas")
def make_ideas(session_id: str, body: IdeasRequest):
    load(session_id)
    start_ideas(session_id, more=body.more)
    return ideas_status(load(session_id))


@app.post("/api/sessions/{session_id}/pick")
def pick_idea(session_id: str, body: PickRequest):
    """The chosen idea becomes the answer to q8, and the one-idea flow takes over."""
    with _locks[session_id]:
        session = load(session_id)
        items = (session.get("ideas") or {}).get("items") or []
        chosen = next((i for i in items if i["id"] == body.idea_id and not i.get("unsafe_reason")), None)
        if chosen is None:
            raise HTTPException(404, "Unknown idea")
        session["answers"]["pick"] = {
            "question": QUESTIONS_BY_ID["pick"]["text"], "value": chosen["title"], "other": None,
            "dont_know": False, "idea_id": chosen["id"], "answered_at": now(),
        }
        save(session)
    return put_answer(session_id, conductor.IDEA_QUESTION, Answer(value=chosen["description"]))


@app.put("/api/sessions/{session_id}/answers/{question_id}/follow_up")
def put_follow_up(session_id: str, question_id: str, body: FollowUpAnswer):
    with _locks[session_id]:
        session = load(session_id)
        answer = session["answers"].get(question_id)
        if answer is None or not answer.get("follow_up"):
            raise HTTPException(404, "No follow-up for this question")
        answer["follow_up"]["answer"] = (body.value or "").strip() or None
        answer["follow_up"]["status"] = "done"
        save(session)
    # A clearer idea description changes how the later questions should read.
    if question_id == conductor.IDEA_QUESTION and answer["follow_up"]["answer"] and conductor.enabled():
        session = run_conductor(session_id, question_id,
                                lambda snap: conductor.plan(snap, allow_follow_up=False))
    if question_id == conductor.IDEA_QUESTION:
        start_research(session_id)
        session = load(session_id)
    if researcher.idea_text(session):
        schedule_check(session_id)
    return {"session": session}


@app.post("/api/sessions/{session_id}/complete")
def complete_session(session_id: str):
    # Let any checks still running finish, so a late follow-up is not lost.
    wait_for_background(session_id, 25.0)
    with _locks[session_id]:
        session = load(session_id)
        session["completed"] = not has_pending_follow_up(session)
        save(session)
    if session["completed"]:
        start_report(session_id)
        session = load(session_id)
    return session


# ---------- the report ----------
#
# Built in the background once the form is finished: wait for research and the
# Checker, gather evidence (Assessor), score it (fixed rules in code), then
# write it up (Writer). The form shows each stage while it waits.

_reports = {}   # session id -> the report thread running in this server

STAGES_TEXT = {
    "research": "Finishing the research on competitors and the market",
    "checking": "Checking your answers against the research",
    "scoring": "Scoring the six areas",
    "writing": "Writing your report",
}


def _set_report(session_id: str, **fields) -> None:
    with _locks[session_id]:
        session = load(session_id)
        report = session.get("report") or {}
        report.update(fields)
        if "stage" in fields:
            report["message"] = STAGES_TEXT.get(fields["stage"], "")
        session["report"] = report
        save(session)


def start_report(session_id: str) -> None:
    thread = _reports.get(session_id)
    if thread and thread.is_alive():
        return
    if not conductor.enabled():
        _set_report(session_id, status="failed", error="The AI is not available, so a report cannot be made.")
        return
    with _locks[session_id]:
        session = load(session_id)
        previous = session.get("report") or {}
        session["report"] = {"status": "preparing", "stage": "research", "message": STAGES_TEXT["research"],
                             "started_at": now(), "previous_verdict": (previous.get("scores") or {}).get("verdict")}
        save(session)
    thread = threading.Thread(target=_report_job, args=(session_id,), daemon=True)
    _reports[session_id] = thread
    thread.start()


def _report_job(session_id: str) -> None:
    log = logging.getLogger("report")
    try:
        # 1. Research still running finishes first (each job has its own time limit).
        start_research(session_id)
        for job in JOBS:
            thread = _research.get((session_id, job))
            if thread:
                thread.join(450)

        # 2. Let the Checker catch up with the final answers and findings.
        _set_report(session_id, stage="checking")
        schedule_check(session_id)
        state = _checks.get(session_id)
        if state:
            state["thread"].join(240)

        # 3. Evidence, reused while the answers and findings are unchanged.
        _set_report(session_id, stage="scoring")
        with _locks[session_id]:
            snapshot = load(session_id)
        calls_before = len(snapshot.get("usage", {}).get("log", []))
        fp = scorer.fingerprint(snapshot)
        saved = snapshot.get("assessment") or {}
        if saved.get("fingerprint") == fp:
            assessment = saved["data"]
        else:
            assessment = scorer.assess(snapshot)
        if assessment is None:
            with _locks[session_id]:
                session = load(session_id)
                merge_usage(session, snapshot, calls_before)
                session["report"].update(status="failed", error="The evidence could not be gathered. Try again.")
                save(session)
            return
        scores = scorer.score(assessment, snapshot)

        # 4. The write-up. If it fails, the report still shows the scores.
        _set_report(session_id, stage="writing")
        text = writer.write(snapshot, scores)

        with _locks[session_id]:
            session = load(session_id)
            merge_usage(session, snapshot, calls_before)
            session["assessment"] = {"fingerprint": fp, "data": assessment}
            session["report"].update(
                status="done", stage=None, message=None, finished_at=now(), fingerprint=fp,
                scores=scores, text=text, writer_failed=text is None,
                alerts=session.get("alerts") or [], facts=session.get("facts") or [],
                competitors=(session.get("research") or {}).get("competitors") or [],
                price_range=(session.get("research") or {}).get("price_range"),
                notes=[n for n in ((session.get("research") or {}).get("market_note"),
                                   (session.get("market_research") or {}).get("market_note")) if n],
                usage={k: (session.get("usage") or {}).get(k) for k in
                       ("calls", "input_tokens", "output_tokens", "searches", "cost_usd")},
            )
            save(session)
    except Exception:
        log.exception("report failed")
        _set_report(session_id, status="failed", error="Something went wrong while making the report. Try again.")


def report_status(session: dict) -> dict:
    report = dict(session.get("report") or {"status": "none"})
    thread = _reports.get(session["id"])
    if report.get("status") == "preparing" and not (thread and thread.is_alive()):
        report.update(status="failed", error="The report was interrupted. Try again.")
    if report.get("status") == "done":
        report["stale"] = report.get("fingerprint") != scorer.fingerprint(session)
    return report


@app.get("/api/sessions/{session_id}/report")
def get_report(session_id: str):
    return report_status(load(session_id))


@app.post("/api/sessions/{session_id}/report")
def make_report(session_id: str):
    load(session_id)
    start_report(session_id)
    return report_status(load(session_id))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
