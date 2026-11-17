#!/bin/bash
# Starts the Business Idea Checker and opens it in the browser.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "First run: setting up (about a minute)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

URL="http://127.0.0.1:8765"
echo "Business Idea Checker is running at $URL  (Ctrl+C to stop)"
(sleep 1.5 && open "$URL") &
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning
