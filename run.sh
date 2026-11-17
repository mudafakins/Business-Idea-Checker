#!/bin/bash
# Starts the Business Idea Checker and opens it in the browser.
# On each start it first fetches the latest version from GitHub, if there is one.
#
# Everything is inside main() so bash reads the whole script before running it:
# an update can safely replace this file while it runs.

main() {
  set -e
  cd "$(dirname "$0")"

  # Update from GitHub: only for a git clone, only a fast-forward, never over local edits.
  if [ -d .git ] && git remote | grep -q .; then
    if [ -z "$(git status --porcelain --untracked-files=no)" ]; then
      echo "Checking for updates..."
      before="$(git rev-parse HEAD)"
      if GIT_TERMINAL_PROMPT=0 git pull --ff-only --quiet 2>/dev/null; then
        if [ "$before" != "$(git rev-parse HEAD)" ]; then
          echo "Updated to $(git describe --tags --always)."
        fi
      else
        echo "Could not check for updates (offline?). Starting the current version."
      fi
    else
      echo "Local changes found, so automatic updates are skipped."
    fi
  fi

  if [ ! -x .venv/bin/pip ]; then
    echo "First run: setting up (about a minute)..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
  fi
  .venv/bin/pip install --quiet -r requirements.txt

  if [ ! -f .env ]; then cp .env.example .env; fi

  URL="http://127.0.0.1:8765"
  echo "Business Idea Checker is running at $URL  (Ctrl+C to stop)"
  if command -v open >/dev/null; then
    (sleep 1.5 && open "$URL") &
  elif command -v xdg-open >/dev/null; then
    (sleep 1.5 && xdg-open "$URL" >/dev/null 2>&1) &
  fi
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
}

main "$@"
