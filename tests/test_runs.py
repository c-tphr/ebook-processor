"""A run in the app, over HTTP: the Stop button and Continue after it. No test calls a model."""

from __future__ import annotations

import threading
import time

from tests.scripted import Script


class Waiting:
    """A stand-in for the model whose answers wait, while ``hold`` is set, until the test lets them go."""

    def __init__(self) -> None:
        self.hold = threading.Event()      # set: an answer waits for ``release``
        self.entered = threading.Event()   # the run has asked the model and is waiting for it
        self.release = threading.Event()
        self.calls = 0
        self.script = Script()

    def backend(self, settings):
        waiting, real = self, self.script.backend()

        class Backend:
            def complete(self, **kwargs):
                waiting.calls += 1
                if waiting.hold.is_set():
                    waiting.entered.set()
                    assert waiting.release.wait(30), "the test never let the model answer"
                return real.complete(**kwargs)

        return Backend()

    def let_go(self) -> None:
        self.hold.clear()
        self.release.set()


def test_a_run_is_stopped_with_the_stop_button_and_goes_on_with_continue(make_web_app):
    model = Waiting()
    model.hold.set()
    app = make_web_app(model=model.backend)
    try:
        job_id = app.add_fixture(density="short")
        at = f"/api/jobs/{job_id}"
        assert model.entered.wait(30), "the run never asked the model"
        assert app.call("GET", at).data["status"] == "running"
        # a run that is going cannot be continued, and a run the app does not have cannot be stopped
        refused = app.call("POST", at + "/continue")
        assert (refused.status, refused.data) == (409, {"error": "this run cannot be continued"})
        assert app.call("POST", "/api/jobs/20260101-000000-nothing/cancel").status == 404
        assert app.call("POST", at + "/cancel", csrf=False).status == 403 and app.call("GET", at).data["status"] == "running"
        # Stop is taken at once; the run shows it when the call that is under way has ended
        stop = app.call("POST", at + "/cancel")
        assert (stop.status, stop.data) == (200, {"ok": True})
        assert app.call("GET", at).data["status"] == "running"
        model.let_go()
        stopped = app.wait(job_id)
        assert stopped["status"] == "stopped" and stopped["message"] == "Stopped. Press Continue to go on where it left off."
        assert "report" not in stopped and stopped["can_read"] is False
        before = model.calls
        for _ in range(500):   # the run's thread lets go of the run a moment after the status says "stopped"
            if job_id not in app.jobs.cancels:
                break
            time.sleep(0.01)
        # nothing is going now: there is nothing to stop
        again = app.call("POST", at + "/cancel")
        assert (again.status, again.data) == (409, {"error": "this run is not going"})
        assert model.calls == before            # and a stopped run asks the model nothing more
        # Continue takes the run up again, and it finishes
        go = app.call("POST", at + "/continue")
        assert (go.status, go.data) == (200, {"ok": True})
        done = app.wait(job_id)
        assert done["status"] == "done" and done["message"] == "Finished" and done["can_read"] is True
        assert done["counts"]["questions"] == 8 and done["report"]["complete"] and model.calls > before
        # a finished run can be neither stopped nor continued
        assert app.call("POST", at + "/cancel").status == 409
        refused = app.call("POST", at + "/continue")
        assert (refused.status, refused.data) == (409, {"error": "this run cannot be continued"})
    finally:
        model.let_go()   # whatever failed, no thread of the run is left waiting for the test


def test_a_run_that_was_stopped_before_its_turn_came_is_continued_too(make_web_app):
    """One book is processed at a time. Stop on a book that still waits for its turn takes effect when the turn comes."""
    model = Waiting()
    model.hold.set()
    app = make_web_app(model=model.backend)
    try:
        first, second = app.add_fixture(density="short"), app.add_fixture(density="short")
        assert model.entered.wait(30), "the first run never asked the model"
        assert app.call("GET", f"/api/jobs/{second}").data["status"] == "queued"
        assert app.call("POST", f"/api/jobs/{second}/cancel").status == 200
        model.let_go()
        assert app.wait(first)["status"] == "done"
        stopped = app.wait(second)
        assert stopped["status"] == "stopped" and stopped["message"] == "Stopped. Press Continue to go on where it left off."
        for _ in range(500):
            if second not in app.jobs.cancels:
                break
            time.sleep(0.01)
        assert app.call("POST", f"/api/jobs/{second}/continue").status == 200
        assert app.wait(second)["status"] == "done"
    finally:
        model.let_go()
