"""Business Idea Checker - local server. Step 1: the form and saved answers."""

import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .questions import QUESTIONS, QUESTIONS_BY_ID, STAGES

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

SESSION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

app = FastAPI(title="Business Idea Checker")


class Answer(BaseModel):
    value: Optional[Union[str, List[str]]] = None
    other: Optional[str] = None
    dont_know: bool = False


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
    return {"stages": STAGES, "questions": QUESTIONS}


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
    return load(session_id)


@app.put("/api/sessions/{session_id}/answers/{question_id}")
def put_answer(session_id: str, question_id: str, answer: Answer):
    question = QUESTIONS_BY_ID.get(question_id)
    if question is None:
        raise HTTPException(404, "Unknown question")
    session = load(session_id)
    session["answers"][question_id] = {
        "question": question["text"],
        "value": answer.value,
        "other": answer.other,
        "dont_know": answer.dont_know,
        "answered_at": now(),
    }
    save(session)
    return session


@app.post("/api/sessions/{session_id}/complete")
def complete_session(session_id: str):
    session = load(session_id)
    session["completed"] = True
    save(session)
    return session


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
