"""Business Idea Checker - local server. The form, saved answers, the Conductor and the Researcher."""

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

from . import conductor, researcher  # noqa: E402  (need the environment loaded first)
from .questions import QUESTIONS, QUESTIONS_BY_ID, STAGES  # noqa: E402

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


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    session = load(session_id)
    if session.get("research"):
        session["research"] = research_status(session)
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
        usage["log"].append(call)


# ---------- background research ----------

_research = {}   # session id -> the research thread running in this server


def start_research(session_id: str, force: bool = False) -> None:
    """Start the competitor research unless it is already running or done for this idea."""
    with _locks[session_id]:
        session = load(session_id)
        idea = researcher.idea_text(session)
        current = session.get("research") or {}
        running = _research.get(session_id)
        if not idea or not conductor.enabled():
            return
        if current.get("idea") == idea and not force:
            if current.get("status") == "done" or (running and running.is_alive()):
                return
        if force and running and running.is_alive() and current.get("idea") == idea:
            return
        session["research"] = {"status": "running", "idea": idea, "started_at": now()}
        save(session)
        snapshot = json.loads(json.dumps(session))

    def job():
        calls_before = len(snapshot.get("usage", {}).get("log", []))
        try:
            result = researcher.run(snapshot)
        except Exception:
            logging.getLogger("researcher").exception("research failed")
            result = None
        with _locks[session_id]:
            session = load(session_id)
            merge_usage(session, snapshot, calls_before)
            research = session.get("research") or {}
            if research.get("idea") == idea:   # the idea has not been changed since
                research["finished_at"] = now()
                if result is None:
                    research["status"] = "failed"
                else:
                    research.update(status="done", competitors=result["competitors"],
                                    price_range=result["price_range"], market_note=result["market_note"])
                    others = [f for f in session.get("facts", []) if f.get("job") != researcher.JOB]
                    session["facts"] = others + result["facts"]
                session["research"] = research
            save(session)

    thread = threading.Thread(target=job, daemon=True)
    _research[session_id] = thread
    thread.start()


def research_status(session: dict) -> dict:
    research = session.get("research") or {"status": "none"}
    # Research that was running when the app was restarted will never finish.
    if research.get("status") == "running":
        thread = _research.get(session["id"])
        if not (thread and thread.is_alive()):
            research = dict(research, status="failed")
    return research


@app.get("/api/sessions/{session_id}/research")
def get_research(session_id: str):
    return research_status(load(session_id))


@app.post("/api/sessions/{session_id}/research")
def retry_research(session_id: str):
    load(session_id)
    start_research(session_id, force=True)
    return research_status(load(session_id))


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

    if conductor.enabled() and not answer.dont_know:
        if question_id == conductor.IDEA_QUESTION:
            # The next questions depend on this, so the form waits for it.
            session = run_conductor(session_id, question_id,
                                    lambda snap: conductor.plan(snap, allow_follow_up=True))
            if not (session["answers"][question_id].get("follow_up") or {}).get("status") == "pending":
                start_research(session_id)
        elif question["type"] in ("text", "long"):
            # Vagueness check and skips: the form does not wait.
            run_in_background(session_id, question_id,
                              lambda snap: conductor.review(snap, question_id))
    return {"session": session}


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
    return {"session": session}


@app.post("/api/sessions/{session_id}/complete")
def complete_session(session_id: str):
    # Let any checks still running finish, so a late follow-up is not lost.
    wait_for_background(session_id, 25.0)
    with _locks[session_id]:
        session = load(session_id)
        session["completed"] = not has_pending_follow_up(session)
        save(session)
    return session


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
