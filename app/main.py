"""VizAgent web app: FastAPI server on 0.0.0.0:$PORT.

Ingress serves this at http://<team-host>/app and strips the /app prefix, so routes
here live at / and the UI must call them with paths relative to the page.
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

import agent
import llm
from vss import VSSClient

logging.basicConfig(level=logging.INFO)

HERE = Path(__file__).parent
vss = VSSClient()
state = {"tracing": False}


@asynccontextmanager
async def lifespan(_app):
    state["tracing"] = llm.init_tracing()
    yield


app = FastAPI(title="VizAgent", lifespan=lifespan)


def _call(fn, *args, **kwargs):
    """Run a VSS call and turn backend failures into clean HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text[:500])
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"VSS backend unreachable: {exc}")


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
    }


@app.post("/api/search")
def search(body: SearchBody):
    return _call(vss.search, **body.model_dump())


@app.post("/api/ask")
def ask(body: AskBody):
    return _call(vss.ask, body.question, body.original_video, body.top_k)


@app.post("/api/triage")
def triage(body: SearchBody):
    filters = body.model_dump(exclude={"query", "top_k"})
    return _call(agent.triage, vss, body.query, body.top_k, **filters)


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
