"""ViZ Agent web app: FastAPI server on 0.0.0.0:$PORT.

Ingress serves this at http://<team-host>/app and strips the /app prefix, so routes
here live at / and the UI must call them with paths relative to the page.
Routes and JSON shapes are documented in API.md.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import extras
import gpu
import llm
import sweep
import workorder
from vss import redact

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO, and the stream URL carries the login token.
logging.getLogger("httpx").setLevel(logging.WARNING)

HERE = Path(__file__).parent
vss = sweep.client
state = {"tracing": False}


@asynccontextmanager
async def lifespan(_app):
    state["tracing"] = llm.init_tracing()
    yield


app = FastAPI(title="ViZ Agent", lifespan=lifespan)


def _call(fn, *args, **kwargs):
    """Run a VSS call and turn backend failures into clean HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except RuntimeError as exc:
        raise HTTPException(503, redact(str(exc)))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, redact(exc.response.text[:500]))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"VSS backend unreachable: {redact(str(exc))}")


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def features():
    """Which extras are on: a VIZ_* switch plus whatever the extra depends on.

    Fixture mode turns the extras on with canned answers so the page can be built offline.
    Re-ingest stays off unless VIZ_REINGEST=1, because it rewrites the team's shared index.
    """
    if sweep.fixture_mode():
        return {"cosmos": True, "publish": True, "watch": True, "context": True, "dispatch": True,
                "reingest": _flag("VIZ_REINGEST", "0")}  # fmt: skip
    live = vss.configured
    return {
        "cosmos": live and gpu.configured(),  # gpu.configured() also honours VIZ_COSMOS=0
        "publish": _flag("VIZ_PUBLISH", "1") and state["tracing"],
        "watch": live and _flag("VIZ_WATCH", "0"),  # off by default: every pass re-runs every search
        "context": live and _flag("VIZ_CONTEXT", "1"),
        "dispatch": extras.dispatch_configured(),
        "reingest": live and _flag("VIZ_REINGEST", "0"),
    }


def _require(feature):
    if not features().get(feature):
        raise HTTPException(503, f"the '{feature}' feature is off here (see /health)")


def _guard(fn, *args, **kwargs):
    """Run an extra and turn upstream failures into clean, token-free HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, redact(str(exc)))
    except RuntimeError as exc:
        raise HTTPException(503, redact(str(exc)))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"upstream returned HTTP {exc.response.status_code}: {redact(exc.response.text[:300])}")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"upstream unreachable: {redact(str(exc))}")


def _conflict_or_404(conflict_id):
    conflict = sweep.get_conflict(conflict_id)
    if conflict is None:
        raise HTTPException(404, "unknown conflict id; run a sweep first")
    return conflict


class SearchBody(BaseModel):
    query: str
    top_k: int = 15
    min_similarity: float = 0.3
    time_filter: str = "all"
    metadata_filters: dict = {}
    tags: list[str] = []


class AskBody(BaseModel):
    question: str
    original_video: str | None = None
    top_k: int = 10


class SweepBody(BaseModel):
    cameras: list[str] | None = None
    top_k: int = 10


class WorkOrderBody(BaseModel):
    conflict_ids: list[str]


class SecondLookBody(BaseModel):
    conflict_id: str


class PublishBody(BaseModel):
    conflict_ids: list[str]
    labels: dict[str, bool] = {}


class WatchBody(BaseModel):
    on: bool


class ProposeBody(BaseModel):
    type: str


class ReingestBody(BaseModel):
    original_video: str
    prompt: str | None = None
    confirm: bool = False


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/health")
def health():
    return {
        "ok": True,
        "vss_configured": vss.configured,
        "llm_configured": llm.configured(),
        "tracing": state["tracing"],
        "gpu_configured": gpu.configured(),
        "mode": "fixture" if sweep.fixture_mode() else "live",
        "features": features(),
    }


# ---- the agent ----


@app.post("/api/sweep")
def start_sweep(body: SweepBody):
    if not sweep.fixture_mode() and not vss.configured:
        raise HTTPException(503, "VSS is not configured: set VSS_URL/VSS_USERNAME/VSS_PASSWORD")
    return {"job_id": sweep.start_job(body.cameras, body.top_k)}


@app.get("/api/sweep/{job_id}")
def sweep_status(job_id: str):
    job = sweep.get_job(job_id)
    if job is None:
        raise HTTPException(404, "no such sweep")
    return job


@app.get("/api/snapshot/{name}")
def snapshot(name: str):
    result = sweep.load_snapshot(name) if name in ("before", "after") else None
    if result is None:
        raise HTTPException(404, f"no '{name}' snapshot saved")
    return result


@app.post("/api/workorder")
def draft_work_order(body: WorkOrderBody):
    conflicts = [c for c in map(sweep.get_conflict, body.conflict_ids) if c]
    if not conflicts:
        raise HTTPException(404, "none of those conflicts are known; run a sweep first")
    return workorder.draft(conflicts)


@app.post("/api/workorder/send")
def send_work_order(body: WorkOrderBody):
    """Post an engineer-approved work order to the team's work-order queue."""
    _require("dispatch")
    conflicts = [c for c in map(sweep.get_conflict, body.conflict_ids) if c]
    if not conflicts:
        raise HTTPException(404, "none of those conflicts are known; run a sweep first")
    order = workorder.draft(conflicts)
    if sweep.fixture_mode():
        return {**order, "status": "SENT", "sent_to": "nowhere (fixture mode)"}
    return _guard(extras.send_work_order, order)


# ---- extras (see /health features) ----


@app.post("/api/second-look")
def second_look(body: SecondLookBody):
    _require("cosmos")
    if sweep.fixture_mode():
        return extras.fixture_second_look(_conflict_or_404(body.conflict_id))
    result = _guard(sweep.second_look, body.conflict_id)
    if result is None:
        raise HTTPException(404, "no such conflict; run a sweep first")
    extras.refresh_latest()  # a second look can change the funnel and the hotspot ranking
    return result


@app.post("/api/publish")
def publish(body: PublishBody):
    _require("publish")
    conflicts = [c for c in map(sweep.get_conflict, body.conflict_ids) if c]
    if not conflicts:
        raise HTTPException(404, "none of those conflicts are known; run a sweep first")
    if sweep.fixture_mode():
        return extras.fixture_publish(conflicts, body.labels)
    try:
        result = sweep.publish(body.conflict_ids, body.labels)
        result["eval_url"] = extras.log_review(conflicts, body.labels)
        result["review"] = extras.review_numbers(conflicts, body.labels)
    except Exception as exc:
        raise HTTPException(502, f"publish failed: {redact(str(exc))[:300]}")
    return result


@app.post("/api/watch")
def watch(body: WatchBody):
    _require("watch")
    if sweep.fixture_mode():
        return {"on": body.on}
    return {"on": sweep.set_watch(body.on)}


@app.get("/api/alerts")
def alerts(since: int = 0):
    return sweep.alerts_since(since)


@app.get("/api/context")
def context(conflict_id: str):
    _require("context")
    return _guard(extras.context, _conflict_or_404(conflict_id))


@app.post("/api/reingest/propose")
def propose_reingest(body: ProposeBody):
    _require("reingest")
    return _guard(extras.propose_prompt, body.type)


@app.post("/api/reingest")
def run_reingest(body: ReingestBody):
    _require("reingest")
    if not body.confirm:
        raise HTTPException(400, "re-ingest rewrites the team's index: send confirm: true")
    if sweep.fixture_mode():
        return {"job_id": "fixture", "status": "fixture mode: nothing was re-ingested"}
    return _guard(extras.start_reingest, body.original_video, body.prompt)


@app.get("/api/reingest/{job_id}")
def reingest_progress(job_id: str):
    _require("reingest")
    if sweep.fixture_mode():
        return {"job_id": job_id, "status": "completed"}
    return _guard(extras.reingest_status, job_id)


# ---- plain VSS passthroughs ----


@app.post("/api/search")
def search(body: SearchBody):
    return _call(vss.search, **body.model_dump())


@app.post("/api/ask")
def ask(body: AskBody):
    return _call(vss.ask, body.question, body.original_video, body.top_k)


@app.get("/api/explore")
def explore(limit: int = 48, offset: int = 0):
    return _call(vss.explore, limit, offset)


@app.get("/api/detections")
def detections(source: str):
    return _call(vss.detections, source)


@app.get("/api/stats")
def stats():
    return _call(vss.dashboard_stats)


@app.get("/api/stream")
def stream(source: str, request: Request):
    """Proxy video playback so the backend JWT never reaches the browser."""
    if sweep.fixture_mode():
        raise HTTPException(404, "no video in fixture mode")
    upstream = _call(vss.open_stream, source, request.headers.get("range"))
    if upstream.status_code >= 400:
        upstream.close()
        raise HTTPException(upstream.status_code, "stream not available")
    passthrough = ("content-type", "content-length", "content-range", "accept-ranges")
    headers = {k: v for k, v in upstream.headers.items() if k.lower() in passthrough}
    return StreamingResponse(
        upstream.iter_bytes(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.close),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
