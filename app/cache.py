"""A small file cache in reports/cache, so the same inputs give the same results.

Used for:
  - research: the same idea in the same place reuses its findings for 7 days,
    so checking it again works from the same data (brief: honesty rules);
  - assessments: the same answers and findings reuse the same evidence, so
    the verdict, worked out by fixed rules from it, is the same every time.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

DIR = Path(__file__).resolve().parent.parent / "reports" / "cache"


def key(*parts) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def get(name: str, max_age_days: Optional[float] = None) -> Optional[dict]:
    path = DIR / (name + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if max_age_days is not None and time.time() - data.get("saved", 0) > max_age_days * 86400:
        return None
    return data


def put(name: str, value: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / (name + ".json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved": time.time(), "saved_on": time.strftime("%Y-%m-%d"), "value": value},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
