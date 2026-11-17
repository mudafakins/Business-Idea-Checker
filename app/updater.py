"""Updates from GitHub, so every copy of the app runs the latest pushed version.

How it works:
  check() - asks the git remote whether a newer version has been pushed
            (at most every 30 minutes unless forced).
  apply() - pulls it (fast-forward only), installs the locked packages, then
            restarts the app in place.

It never overwrites local edits to the code: if a copy has changed files, it
says so and leaves it alone. Saved reports and .env are not tracked by git, so
an update never touches them. Copies not made with `git clone` are skipped.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CHECK_EVERY = 30 * 60
_cache = {"at": 0.0, "result": None}
_lock = threading.Lock()


def _git(*args, timeout: float = 30.0) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                          env=env, stdin=subprocess.DEVNULL)


def _out(*args) -> str:
    try:
        done = _git(*args)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def enabled() -> bool:
    return (ROOT / ".git").exists() and bool(_out("remote"))


def current() -> dict:
    return {"version": _out("describe", "--tags", "--always"), "commit": _out("rev-parse", "--short", "HEAD")}


def _upstream() -> str:
    return _out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "origin/main"


def _local_changes() -> bool:
    # Tracked files only: saved reports, .env and the virtual environment are ignored.
    return bool(_out("status", "--porcelain", "--untracked-files=no"))


def check(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["result"] and time.time() - _cache["at"] < CHECK_EVERY:
            return _cache["result"]
        result = {"enabled": enabled(), "available": False, **current()}
        if result["enabled"]:
            try:
                fetched = _git("fetch", "--quiet", "--tags", "origin", timeout=30)
                ok = fetched.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if not ok:
                result["error"] = "Could not reach GitHub to check for updates."
            else:
                up = _upstream()
                behind = int(_out("rev-list", "--count", "HEAD..%s" % up) or 0)
                result.update(
                    available=behind > 0,
                    behind=behind,
                    latest=_out("describe", "--tags", "--always", up),
                    changes=[line for line in _out("log", "--format=%s", "-n", "6", "HEAD..%s" % up).splitlines() if line],
                    local_changes=_local_changes(),
                )
        _cache.update(at=time.time(), result=result)
        return result


def apply() -> Optional[str]:
    """Pulls and installs the update. Returns an error message, or None when it worked."""
    if not enabled():
        return "This copy was not set up with git clone, so it cannot update itself."
    if _local_changes():
        return "This copy has local changes to the app's files, so it was not updated. Ask whoever changed them."
    try:
        pulled = _git("pull", "--ff-only", "--quiet", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return "The update could not be downloaded. Check the internet connection and try again."
    if pulled.returncode != 0:
        return "The update could not be applied: " + (pulled.stderr or pulled.stdout).strip()[:300]
    try:
        installed = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")],
                                   cwd=ROOT, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return "The update was downloaded, but its packages could not be installed. Restart the app with ./run.sh."
    if installed.returncode != 0:
        return "The update was downloaded, but its packages could not be installed: " + installed.stderr.strip()[-300:]
    _cache.update(at=0.0, result=None)
    return None


def restart_soon(delay: float = 1.0) -> None:
    """Replaces this process with a fresh copy of the same server, running the new code."""
    def go():
        os.execv(sys.executable, [sys.executable, "-m", "uvicorn"] + sys.argv[1:])
    threading.Timer(delay, go).start()
