"""Tests for the CAUSE line, cause-aware work orders and the extras (all offline)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from test_app import client  # noqa: F401  (shared fixture)


@pytest.mark.parametrize(
    ("caption", "cause", "letter"),
    [
        ("Van parked at the corner hides her.\nCAUSE: blocked_view\nVERDICT: B", "blocked_view", "B"),
        ("cause: turning_vehicle. VERDICT: (A)", "turning_vehicle", "A"),
        ("Then a line CAUSE: blocked_view, turning_vehicle, no_crosswalk, did_not_slow or none.", None, None),
        ("CAUSE: none\nVERDICT: D", None, "D"),  # 'none' is not a cause
        ("CAUSE: made_up_reason VERDICT: B", None, "B"),
        ("An original description with no lines.", None, None),
    ],
)
def test_stored_signal_reads_cause(caption, cause, letter):
    import sweep

    assert sweep.stored_signal(caption) == {"letter": letter, "cause": cause}


def test_reingest_prompt_has_cause_and_verdict_lines():
    import taxonomy

    prompt = taxonomy.REINGEST_PROMPT
    assert len(prompt) <= 800
    assert "CAUSE:" in prompt and prompt.endswith("VERDICT: A, B, C or D.")
    assert set(taxonomy.CAUSES) == set(taxonomy.CAUSE_FIXES)


def test_fixes_follow_the_cause_first():
    import taxonomy

    assert taxonomy.fixes_for("failure_to_yield", "blocked_view")[0]["name"].startswith("Daylighting")
    assert taxonomy.fixes_for("failure_to_yield", None)[0]["name"] == "Leading pedestrian interval"
    assert taxonomy.fixes_for("midblock_crossing", "did_not_slow")[0]["name"] == "Speed safety camera"


def _conflict(cid, cause=None, severity=2, view="fixed"):
    return {
        "id": cid, "source": f"s3://b/{cid}.mp4", "original_video": "s3://b/v.mp4", "camera_id": "sf_streets_cam-2",
        "location": "san_francisco", "view": view, "start_sec": 65, "end_sec": 70, "similarity": 0.6,
        "caption": "...", "type": {"key": "failure_to_yield", "label": "Failure to yield at a crosswalk"},
        "signals": {"stored": {"letter": "B", "cause": cause}, "yolo": {"ok": True, "note": "1 person, 1 vehicle"},
                    "cosmos": None},
        "status": "verified", "reject_reason": None, "severity": severity, "reason": "r", "hotspot_key": "sf_streets_cam-2",
    }  # fmt: skip


def test_work_order_picks_fix_by_cause_with_fhwa_effect():
    import workorder

    order = workorder.draft([_conflict("a", "turning_vehicle"), _conflict("b", "turning_vehicle"), _conflict("c")])
    assert order["cause"] == "turning_vehicle"
    assert order["countermeasure"] == "Leading pedestrian interval"
    assert "13%" in order["expected_effect"]
    assert "Physical cause (Cosmos Reason)" in order["markdown"] and "Expected effect" in order["markdown"]
    assert order["open311"]["attribute"]["cause"] == "turning_vehicle"
    no_cause = workorder.draft([_conflict("d")])
    assert no_cause["cause"] is None and no_cause["countermeasure"] == "Leading pedestrian interval"


def test_parse_think_answer():
    import gpu

    assert gpu.parse_think_answer("<think>Car turns into her path.</think>\n<answer>\nB\n</answer>") == (
        "Car turns into her path.",
        "B",
    )
    assert gpu.parse_think_answer("no tags, final answer (D)")[1] == "D"
    assert gpu.parse_think_answer("<think>unsure</think>") == ("unsure", None)
    assert gpu.parse_think_answer(None) == ("", None)


@pytest.fixture
def live(client, monkeypatch):  # noqa: F811
    """A live-mode app with one known conflict and every dependency faked."""
    import main
    import sweep

    class Fake:
        configured = True

        def segment_bytes(self, source):
            return b"mp4"

        def segments(self, original_video):
            return {"segments": [
                {"source": "s3://b/x3.mp4", "start_sec": 10},
                {"source": "s3://b/x1.mp4", "start_sec": 0},
                {"source": "s3://b/x2.mp4", "start_sec": 5},
            ]}  # fmt: skip

        def reingest(self, original_video, prompt, chunk_count=1):
            assert chunk_count == 1 and len(prompt) <= 800
            return {"job_id": "j1", "original_video": original_video}

        def reingest_status(self, job_id):
            return {"job_id": job_id, "status": "completed"}

    monkeypatch.setattr(sweep, "client", Fake())
    monkeypatch.setattr(main, "vss", sweep.client)
    monkeypatch.setenv("COSMOS3_REASON_URL", "http://gpu.test:8001")
    monkeypatch.setenv("VIZ_REINGEST", "1")
    conflict = _conflict("x2", "blocked_view")
    conflict["source"] = "s3://b/x2.mp4"
    sweep.remember({"conflicts": [conflict]})
    return client, conflict


def test_second_look_can_overrule_the_sweep(live, monkeypatch):
    import gpu

    api, conflict = live
    monkeypatch.setattr(gpu, "cosmos_verify", lambda clip, prompt: ("car waited", "C"))
    body = api.post("/api/second-look", json={"conflict_id": "x2"}).json()
    assert body["letter"] == "C" and body["agrees"] is False and body["trace"] == "car waited"
    assert conflict["status"] == "rejected" and conflict["severity"] == 0
    assert conflict["reject_reason"].startswith("Cosmos second look")
    assert api.post("/api/second-look", json={"conflict_id": "nope"}).status_code == 404


def test_context_orders_segments_by_start_time(live):
    api, _ = live
    body = api.get("/api/context", params={"conflict_id": "x2"}).json()
    assert (body["prev"]["source"], body["this"]["source"], body["next"]["source"]) == (
        "s3://b/x1.mp4",
        "s3://b/x2.mp4",
        "s3://b/x3.mp4",
    )


def test_reingest_needs_confirmation_and_falls_back_to_official_prompt(live):
    import taxonomy

    api, _ = live
    proposal = api.post("/api/reingest/propose", json={"type": "midblock_crossing"}).json()
    assert proposal["prompt"] == taxonomy.REINGEST_PROMPT and proposal["chars"] <= 800  # no LLM configured
    assert api.post("/api/reingest/propose", json={"type": "nope"}).status_code == 400
    assert api.post("/api/reingest", json={"original_video": "s3://b/v.mp4"}).status_code == 400
    started = api.post("/api/reingest", json={"original_video": "s3://b/v.mp4", "confirm": True}).json()
    assert started["job_id"] == "j1"
    assert api.get("/api/reingest/j1").json()["status"] == "completed"


def test_extras_are_off_without_their_dependencies(client):  # noqa: F811
    assert client.post("/api/second-look", json={"conflict_id": "x"}).status_code == 503
    assert client.post("/api/watch", json={"on": True}).status_code == 503
    assert client.post("/api/reingest", json={"original_video": "v", "confirm": True}).status_code == 503




def test_fixture_mode_turns_extras_on_with_canned_answers(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VIZ_MODE", "fixture")
    features = client.get("/health").json()["features"]
    assert features["cosmos"] and features["publish"] and features["watch"] and not features["reingest"]
    job = client.get(f"/api/sweep/{client.post('/api/sweep', json={}).json()['job_id']}").json()
    first = job["result"]["conflicts"][0]["id"]
    look = client.post("/api/second-look", json={"conflict_id": first}).json()
    assert look["letter"] == "B" and "Fixture mode" in look["trace"]
    pub = client.post("/api/publish", json={"conflict_ids": [first], "labels": {first: True}}).json()
    assert pub["rows"] == 1 and pub["precision"] == 1.0
    assert client.get("/api/context", params={"conflict_id": first}).json()["this"]["source"]


def test_app_folder_fits_in_a_configmap():
    app = Path(__file__).resolve().parents[1] / "app"
    size = sum(p.stat().st_size for p in app.iterdir() if p.is_file())
    assert size < 900_000, size
    assert not any(p.is_dir() and p.name != "__pycache__" for p in app.iterdir())  # ConfigMaps are flat


def test_no_token_in_error_text(live, monkeypatch):
    import httpx

    import sweep

    def boom(source):
        raise httpx.ConnectError("GET http://vss/api/v1/videos/stream?source=x&token=SECRET123 failed")

    monkeypatch.setattr(sweep.client, "segment_bytes", boom)
    api, _ = live
    resp = api.post("/api/second-look", json={"conflict_id": "x2"})
    assert resp.status_code == 502 and "SECRET123" not in resp.text
