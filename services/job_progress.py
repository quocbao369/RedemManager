"""In-memory progress tracker for background instance jobs.

The Open flow runs in a BackgroundTask, so the browser gets an immediate
"launching" reply and never sees the intermediate steps. The frontend polls
this while a job runs, which is how per-step messages (such as the rotated
exit IP) reach the user.
"""
import threading
import time
from typing import Any, Dict, List, Optional


_lock = threading.Lock()
_jobs: Dict[int, Dict[str, Any]] = {}

MAX_STEPS = 40


def start_job(instance_id: int) -> None:
    with _lock:
        _jobs[instance_id] = {"started": time.time(), "done": False,
                               "steps": []}


def step(instance_id: int, message: str, level: str = "info") -> None:
    with _lock:
        job = _jobs.get(instance_id)
        if job is None:
            return
        job["steps"].append({"ts": time.time(), "message": message,
                             "level": level})
        if len(job["steps"]) > MAX_STEPS:
            del job["steps"][:len(job["steps"]) - MAX_STEPS]


def finish_job(instance_id: int, ok: bool = True) -> None:
    with _lock:
        job = _jobs.get(instance_id)
        if job is None:
            return
        job["done"] = True
        job["ok"] = ok
        job["finished"] = time.time()


def get_job(instance_id: int, since: float = 0.0) -> Optional[Dict[str, Any]]:
    """Return the job state, optionally only steps newer than `since`."""
    with _lock:
        job = _jobs.get(instance_id)
        if job is None:
            return None
        steps: List[Dict[str, Any]] = [s for s in job["steps"] if s["ts"] > since]
        return {
            "running": not job["done"],
            "done": job["done"],
            "ok": job.get("ok"),
            "started": job.get("started"),
            "steps": steps,
        }


def cleanup(max_age: float = 1800) -> None:
    """Drop jobs that finished long ago so the dict cannot grow forever."""
    now = time.time()
    with _lock:
        for key in list(_jobs):
            job = _jobs[key]
            if job["done"] and now - job.get("finished", now) > max_age:
                del _jobs[key]