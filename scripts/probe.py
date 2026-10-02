"""Print the shape of real VSS responses so the engine can be built against them.

Run on the workshop VM from the repo root:  python scripts/probe.py
Output is safe to paste: bucket names, the team name, hosts and tokens are masked.
Nothing is written to disk.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import httpx

from vss import VSSClient

DASHCAM = "pie_cam-3"
QUERY = "pedestrian crossing in front of the car"


def mask(text):
    text = re.sub(r"s3://[^/\s\"']+", "s3://<bucket>", text)
    text = re.sub(r"https?://[^/\s\"']+", "http://<host>", text)
    text = re.sub(r"(token=|Bearer\s+)[\w.\-]+", r"\1<redacted>", text)
    team = os.environ.get("VSS_USERNAME") or os.environ.get("USERNAME")
    return text.replace(team, "<team>") if team else text


def shape(obj, depth=0, max_depth=5):
    """Compact view of a JSON value: keys, types and short masked samples."""
    if isinstance(obj, dict):
        if depth >= max_depth:
            return f"{{…{len(obj)} keys}}"
        return {k: shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        if not obj:
            return []
        return [f"list[{len(obj)}]", shape(obj[0], depth + 1, max_depth)]
    if isinstance(obj, str):
        return mask(obj if len(obj) <= 160 else obj[:160] + f"…(+{len(obj) - 160} chars)")
    return obj


def show(title, fn):
    print(f"\n===== {title} =====")
    try:
        result = fn()
        print(json.dumps(shape(result), indent=1, default=str)[:6000])
        return result
    except httpx.HTTPStatusError as exc:
        print(f"HTTP {exc.response.status_code}: {mask(exc.response.text[:300])}")
    except Exception as exc:
        print(f"{type(exc).__name__}: {mask(str(exc))[:300]}")
    return None


def gpu_check():
    """Report which model endpoints answer. Prints status codes only."""
    token = os.environ.get("GPU_BEARER_TOKEN")
    out = {"GPU_BEARER_TOKEN_set": bool(token)}
    for name, path in (("COSMOS3_REASON_URL", "/v1/models"), ("YOLO_URL", "/healthz")):
        url = os.environ.get(name)
        if not url:
            out[name] = "not set"
            continue
        for label, headers in (("no_auth", {}), ("bearer", {"Authorization": f"Bearer {token}"} if token else None)):
            if headers is None:
                continue
            try:
                out[f"{name} {label}"] = httpx.get(url.rstrip("/") + path, headers=headers, timeout=10).status_code
            except Exception as exc:
                out[f"{name} {label}"] = type(exc).__name__
    return out


def main():
    vss = VSSClient()
    if not vss.configured:
        sys.exit("VSS is not configured: source /config/<team>.config first")

    show("cameras (metadata/values?field=camera_id)", lambda: vss.metadata_values("camera_id", limit=100))
    show("filterable fields (metadata/schema)", lambda: [f.get("name") for f in vss.metadata_schema().get("schema", [])])

    search = show(
        f"search '{QUERY}' on {DASHCAM} (first hit, first chunk)",
        lambda: _trim(vss.search(QUERY, top_k=3, llm_top_n=0, metadata_filters={"camera_id": DASHCAM})),
    )
    hit = ((search or {}).get("results") or [{}])[0]
    source, parent = hit.get("source"), hit.get("original_video")

    if source:
        show("detections for that hit (videos/detections)", lambda: vss.detections(source))
        show("segment row for that hit (videos/metadata)", lambda: vss.segment_metadata(source))
    if parent:
        show(
            "segments of its parent video (tools/segments)",
            lambda: vss._request("GET", "tools/segments", params={"original_video": parent}),
        )
    show("explore (2 items)", lambda: vss.explore(limit=2))
    show("dashboard overview + metadata", lambda: _pick(vss.dashboard_stats(), "overview", "metadata", "objects"))

    def models():
        import llm

        return llm.list_models()

    show("W&B inference models", models)
    show("GPU endpoints (status codes only)", gpu_check)
    print("\n===== done: paste everything above =====")


def _trim(search):
    return {
        "keys": sorted(search),
        "results": search.get("results", [])[:1],
        "chunk_results": search.get("chunk_results", [])[:1],
    }


def _pick(obj, *keys):
    return {k: obj.get(k) for k in keys}


if __name__ == "__main__":
    main()
