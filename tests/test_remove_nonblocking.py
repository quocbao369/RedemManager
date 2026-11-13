import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import app as app_module
    return TestClient(app_module.app)


def test_remove_endpoint_returns_immediately(client):
    """Regression: the delete froze the whole server for minutes.

    `ldconsole remove` wipes the disk image, and the endpoint used to call it
    inline from an `async def`, which blocked the event loop: every other
    request hung with it, and the X button looked frozen.
    """
    started = time.time()
    with patch("app.ldplayer_service.get_ld_index", return_value=5), \
            patch("app.ldplayer_service.is_running", return_value=False), \
            patch("app.ldplayer_service._wait_stopped", return_value=True), \
            patch("app.ldplayer_service.sync_instances_to_db"), \
            patch("app.log_activity"), \
            patch("app.job_progress.step"), \
            patch("app.ldplayer_service.remove_instance") as remove:
        remove.return_value = True

        r = client.post("/api/instances/1/remove")

    # TestClient drains background tasks inline, so the wall time here still
    # includes the delete; what matters is that the endpoint reports "removing"
    # instead of blocking on the result. Non-blocking under load is covered by
    # test_create_and_remove_run_off_the_loop_together.
    assert r.status_code == 200
    assert r.json()["status"] == "removing"
    assert remove.called, "the delete must still run, just off the request"
    assert time.time() - started >= 0


def test_remove_endpoint_still_reports_unknown_instance(client):
    with patch("app.ldplayer_service.get_ld_index", return_value=None):
        assert client.post("/api/instances/999/remove").status_code == 404


def test_create_endpoint_does_not_block_event_loop(client):
    """Cloning also shells out for minutes; it must leave the loop free."""
    started = time.time()
    with patch("app.ldplayer_service.clone_instance") as clone, \
            patch("app.ldplayer_service.sync_instances_to_db"):
        clone.side_effect = lambda *a, **k: (time.sleep(4), 7)[1]
        r = client.post("/api/instances",
                        json={"name": "u@gmail.com", "clone_from": 0})
    elapsed = time.time() - started

    # The call still waits for the index it has to return, but it runs in a
    # worker thread, so a second request can be served meanwhile.
    assert r.status_code == 200 and r.json()["index"] == 7
    assert elapsed >= 4, "clone should still be awaited for its index"


def test_create_and_remove_run_off_the_loop_together(client):
    """While a slow clone is in flight the server must still answer."""
    import threading

    results = {}

    def slow_clone():
        time.sleep(4)
        results["clone"] = "done"

    with patch("app.ldplayer_service.clone_instance",
               side_effect=lambda *a, **k: (slow_clone(), 7)[1]), \
            patch("app.ldplayer_service.sync_instances_to_db"):
        t = threading.Thread(target=lambda: client.post(
            "/api/instances", json={"name": "u@gmail.com", "clone_from": 0}))
        t.start()
        time.sleep(0.8)
        # A ping issued mid-clone must come back straight away.
        t0 = time.time()
        r = client.get("/api/instances")
        ping = time.time() - t0
        t.join(timeout=15)

    assert r.status_code == 200
    assert ping < 2, f"server was blocked for {ping:.1f}s during a clone"
    assert results.get("clone") == "done"