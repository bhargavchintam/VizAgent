import logging
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

ENV = (
    "VSS_URL", "INGRESS_URL", "VSS_USERNAME", "USERNAME", "VSS_PASSWORD", "PASSWORD",
    "WANDB_API_KEY", "VIZ_MODE",
)  # fmt: skip


@pytest.fixture
def client(monkeypatch):
    for var in ENV:
        monkeypatch.delenv(var, raising=False)
    import importlib

    import main
    import sweep

    importlib.reload(sweep)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def test_health_reports_unconfigured(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert (body["vss_configured"], body["llm_configured"], body["tracing"]) == (False, False, False)
    assert body["mode"] == "live"
    assert set(body["features"]) == {"cosmos", "publish", "watch"}


def test_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "VizAgent" in resp.text or "ViZ" in resp.text


def test_search_without_vss_is_503(client):
    assert client.post("/api/search", json={"query": "person near a car"}).status_code == 503
    assert client.post("/api/sweep", json={}).status_code == 503


def test_httpx_request_logging_is_off(client):
    assert logging.getLogger("httpx").level >= logging.WARNING  # request URLs carry token=


def test_extract_json_handles_fences_think_and_chatter():
    import llm

    assert llm.extract_json('```json\n{"match": true, "severity": 4}\n```') == {"match": True, "severity": 4}
    assert llm.extract_json('<think>{"draft": 1}</think>Sure: {"severity": 2} done') == {"severity": 2}
    with pytest.raises(ValueError):
        llm.extract_json("no json here")


def test_redact_strips_tokens():
    from vss import redact

    out = redact("GET /videos/stream?source=x&token=abc.DEF-123 Authorization: Bearer zzz.yyy")
    assert "abc.DEF-123" not in out and "zzz.yyy" not in out


def test_reingest_prompt_fits_the_api_limit():
    import taxonomy

    assert len(taxonomy.REINGEST_PROMPT) <= 800
    assert "VERDICT" in taxonomy.REINGEST_PROMPT


@pytest.mark.parametrize(
    ("caption", "letter"),
    [
        ("A pedestrian crosses. VERDICT: B", "B"),
        ("Cars wait.\nverdict: (C)", "C"),
        ("VZ_VERDICT: A", "A"),
        ("End with the line VERDICT: A, B, C or D.", None),  # the prompt echoed back, not an answer
        ("VERDICT: A, B, C or D. ... VERDICT: D", "D"),
        ("The verdict: a pedestrian walks by.", None),
        ("No verdict in an original description.", None),
        (None, None),
    ],
)
def test_stored_signal(caption, letter):
    import sweep

    assert sweep.stored_signal(caption)["letter"] == letter


OK = {"ok": True, "note": "1 person, 1 vehicle"}
NO_PERSON = {"ok": False, "note": "detector saw no person in the clip"}
NO_DATA = {"ok": None, "note": "no detector data"}


@pytest.mark.parametrize(
    ("stored", "yolo", "cosmos", "graded", "status"),
    [
        ("B", OK, None, None, "verified"),
        ("A", OK, None, None, "verified"),
        ("B", NO_DATA, None, None, "unverified"),
        ("B", NO_PERSON, None, None, "rejected"),
        ("C", OK, None, None, "rejected"),
        ("D", OK, None, None, "rejected"),
        (None, OK, None, {"conflict": True}, "verified"),
        (None, OK, None, {"conflict": False}, "rejected"),
        (None, NO_DATA, None, {"conflict": True}, "unverified"),
        (None, OK, None, None, "unverified"),
        ("C", NO_PERSON, {"letter": "A"}, None, "verified"),  # a live second look outranks the rest
        ("B", OK, {"letter": "D"}, None, "rejected"),
    ],
)
def test_decide_truth_table(stored, yolo, cosmos, graded, status):
    import sweep

    got, reason = sweep.decide({"letter": stored}, yolo, cosmos, graded)
    assert got == status
    assert (reason is not None) == (status == "rejected")


def test_yolo_signal_dashcam_needs_only_a_person():
    import sweep

    frames = {"width": 1920, "height": 1080, "frames": [{"detections": [
        {"label": "person", "bbox": [900, 300, 1000, 840]},
    ]}]}  # fmt: skip
    dashcam = sweep.yolo_signal(frames, "dashcam")
    assert dashcam["ok"] is True and dashcam["closeness"] == 0.5
    assert sweep.yolo_signal(frames, "fixed")["ok"] is False  # a fixed camera also needs a vehicle


def test_yolo_signal_fixed_camera_closeness_and_rejection():
    import sweep

    touching = [{"class_name": "Person", "box": {"x1": 100, "y1": 100, "x2": 150, "y2": 300}},
                {"class_name": "car", "box": {"x1": 140, "y1": 150, "x2": 400, "y2": 320}}]  # fmt: skip
    signal = sweep.yolo_signal({"objects": touching}, "fixed")
    assert signal["ok"] is True and signal["closeness"] == 1.0 and signal["note"] == "1 person, 1 vehicle"

    only_cars = sweep.yolo_signal({"object_counts": '{"car": 3, "person": 0}'}, "fixed")
    assert only_cars["ok"] is False and "no person" in only_cars["note"]


@pytest.mark.parametrize("garbage", [None, {}, [], "nope", 42, {"frames": [[1, 2], {"a": None}]}])
def test_yolo_signal_survives_garbage(garbage):
    import sweep

    assert sweep.yolo_signal(garbage, "fixed")["ok"] is None


def test_yolo_signal_falls_back_to_the_hit_summary():
    import sweep

    assert sweep.yolo_signal(None, "dashcam", {"object_classes": ["person", "car"]})["ok"] is True


CAPTIONS = {
    "s3://b/seg_1.mp4": "Pedestrian crosses mid-block ahead of the moving camera car. VERDICT: B",
    "s3://b/seg_2.mp4": "Vehicles wait while pedestrians cross with clear space. VERDICT: C",
    "s3://b/seg_3.mp4": "A van blocks the crosswalk. VERDICT: B",
}


class FakeVSS:
    """Stands in for the VSS backend: two dashcam clips and one from a street camera."""

    configured = True
    def login(self):
        return "token"

    def metadata_values(self, field, **_):
        return {"values": ["pie_cam-3", "sdg_warehouse_cam-2"]}

    def search(self, query, **kwargs):
        assert kwargs["metadata_filters"] == {"camera_id": "pie_cam-3"}  # never the warehouse
        return {"results": [
            {"source": s, "similarity_score": 0.5 + i / 10, "reasoning_content": c,
             "original_video": "s3://b/drive_7.mp4", "start_sec": 65}
            for i, (s, c) in enumerate(CAPTIONS.items())
        ]}  # fmt: skip

    def detections(self, source):
        if source.endswith("seg_3.mp4"):
            return {"detections": [{"label": "truck", "bbox": [0, 0, 10, 10]}]}
        if source.endswith("seg_2.mp4"):
            request = httpx.Request("GET", "http://vss/detections")
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return {"detections": [{"label": "person", "bbox": [0, 0, 10, 40]}, {"label": "car", "bbox": [12, 0, 60, 40]}]}


def _wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/sweep/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("sweep did not finish")


def test_sweep_end_to_end_then_work_order(client, monkeypatch):
    import main
    import sweep

    monkeypatch.setattr(sweep, "client", FakeVSS())
    monkeypatch.setattr(main, "vss", sweep.client)

    job = _wait(client, client.post("/api/sweep", json={}).json()["job_id"])
    assert job["status"] == "done", job.get("error")
    assert len(job["steps"]) >= 3
    result = job["result"]
    assert result["cameras"] == ["pie_cam-3"]
    assert result["funnel"] == {"candidates": 3, "verified": 1, "unverified": 0, "rejected": 2}

    by_source = {c["source"]: c for c in result["conflicts"]}
    verified = by_source["s3://b/seg_1.mp4"]
    assert verified["status"] == "verified" and verified["severity"] == 2 and verified["view"] == "dashcam"
    assert by_source["s3://b/seg_2.mp4"]["reject_reason"] == "Cosmos verdict at ingest: normal yielding"
    assert by_source["s3://b/seg_3.mp4"]["reject_reason"] == "Detector saw no person in the clip"
    assert result["conflicts"][0]["id"] == verified["id"]  # verified first
    assert "_hit" not in verified

    (hotspot,) = result["hotspots"]
    assert hotspot["kind"] == "drive" and hotspot["label"] == "Drive drive_7.mp4"
    assert hotspot["conflict_ids"] == [verified["id"]] and hotspot["score"] == 2

    assert client.get("/api/sweep/latest").json()["job_id"] == job["job_id"]

    order = client.post("/api/workorder", json={"conflict_ids": [verified["id"], "unknown"]}).json()
    assert order["status"] == "DRAFT" and order["id"].startswith("VZ-")
    assert {"service_code", "service_name", "description", "address_string", "media_url"} <= set(order["open311"])
    assert "lat" not in order["open311"] and "long" not in order["open311"]  # GPS is never invented
    assert "Drive drive_7.mp4 @ 1:05" in order["markdown"]
    assert client.post("/api/workorder", json={"conflict_ids": ["unknown"]}).status_code == 404


def test_fixture_mode_replays_the_sample(client, monkeypatch):
    monkeypatch.setenv("VIZ_MODE", "fixture")
    assert client.get("/health").json()["mode"] == "fixture"
    job = client.get(f"/api/sweep/{client.post('/api/sweep', json={}).json()['job_id']}").json()
    assert job["status"] == "done"
    funnel, conflicts = job["result"]["funnel"], job["result"]["conflicts"]
    assert funnel["candidates"] == len(conflicts)
    assert funnel["verified"] == sum(c["status"] == "verified" for c in conflicts)
    assert client.post("/api/workorder", json={"conflict_ids": [conflicts[0]["id"]]}).status_code == 200
    assert client.get("/api/stream", params={"source": "s3://x/y.mp4"}).status_code == 404
    assert client.get("/api/snapshot/nope").status_code == 404


@pytest.mark.parametrize(
    ("reply", "letter"),
    [
        ("<think>The car turns as the pedestrian crosses.</think><answer>B</answer>", "B"),
        ("<think>Clear space.</think>\n<answer>(C)</answer>", "C"),
        ("<think>The pedestrian steps back.</think> A", "A"),
        ("no structure at all", None),
        (None, None),
    ],
)
def test_parse_think_answer(reply, letter):
    import gpu

    trace, got = gpu.parse_think_answer(reply)
    assert got == letter and len(trace) <= 1500


def test_second_look_overrides_and_extras_are_gated(client, monkeypatch):
    import gpu
    import main
    import sweep

    fake = FakeVSS()
    fake.segment_bytes = lambda source, max_mb=12: b"mp4"
    monkeypatch.setattr(sweep, "client", fake)
    monkeypatch.setattr(main, "vss", fake)

    assert client.get("/health").json()["features"] == {"cosmos": False, "publish": False, "watch": True}
    assert client.post("/api/second-look", json={"conflict_id": "x"}).status_code == 503
    assert client.post("/api/publish", json={"conflict_ids": ["x"]}).status_code == 503

    job = _wait(client, client.post("/api/sweep", json={}).json()["job_id"])
    verified = job["result"]["conflicts"][0]

    monkeypatch.setenv("COSMOS3_REASON_URL", "http://gpu.invalid")
    monkeypatch.setattr(gpu, "cosmos_verify", lambda clip, question: ("Vehicles waited.", "C"))
    look = client.post("/api/second-look", json={"conflict_id": verified["id"]}).json()
    assert look["letter"] == "C" and look["agrees"] is False and look["status"] == "rejected"
    assert look["conflict"]["reject_reason"] == "Cosmos second look: normal yielding"
    assert client.post("/api/second-look", json={"conflict_id": "unknown"}).status_code == 404

    assert client.get("/api/alerts").json() == {"alerts": [], "cursor": 0, "on": False}


def test_watch_alerts_only_on_new_verified_clips(client, monkeypatch):
    import sweep

    monkeypatch.setattr(sweep, "client", FakeVSS())
    sweep._watch["seen"].clear()
    assert [c["source"] for c in sweep._watch_pass()] == ["s3://b/seg_1.mp4"]
    assert sweep._watch_pass() == []  # nothing new the second time round
