"""The ViZ Agent sweep: find conflict candidates, check each one, decide, grade and rank.

Run on the VM to save a snapshot the app can replay:
    python app/sweep.py --out app/snap_after.json
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

import gpu
import llm
import taxonomy
from vss import VSSClient, redact

log = logging.getLogger("vizagent.sweep")

HERE = Path(__file__).parent
SNAPSHOTS = {name: HERE / f"snap_{name}.json" for name in ("before", "after", "sample")}
CAMERA_PREFIXES = ("pie_cam", "sf_streets_cam")
DEFAULT_CAMERAS = ["pie_cam-3"]
MIN_SIMILARITY = float(os.environ.get("VIZ_MIN_SIMILARITY", "0.3"))
MAX_CANDIDATES = 60
CAPTION_LIMIT = 600
PEOPLE = {"person", "pedestrian"}
VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle", "van", "vehicle"}
STATUS_ORDER = {"verified": 0, "unverified": 1, "rejected": 2}

# One shared client. It is never passed into a traced op: ops take plain values only,
# so credentials cannot end up in a trace.
client = VSSClient()

_jobs = {}
_conflicts = {}
_latest = {"job_id": None}
_watch = {"on": False, "seen": set(), "alerts": []}
WATCH_SECONDS = 45
_lock = threading.Lock()


def fixture_mode():
    return os.environ.get("VIZ_MODE") == "fixture"


def view_of(camera_id):
    return "dashcam" if camera_id.startswith("pie_") else "fixed"


def _first(node, *keys, default=None):
    for key in keys:
        value = node.get(key)
        if value not in (None, ""):
            return value
    return default


def _step(job_id, text):
    log.info("sweep %s: %s", job_id or "cli", text)
    job = _jobs.get(job_id)
    if job is not None:
        job["steps"].append({"t": round(time.monotonic() - job["_started"], 1), "text": text})


# ---- 1. candidates ----


def discover_cameras():
    """Street and dashcam cameras present in the index, so SF cameras join in once they are loaded."""
    try:
        values = client.metadata_values("camera_id").get("values", [])
    except Exception as exc:
        log.warning("camera discovery failed: %s", redact(str(exc)))
        values = []
    cameras = sorted(v for v in values if isinstance(v, str) and v.startswith(CAMERA_PREFIXES))
    return cameras or DEFAULT_CAMERAS


@llm.op
def find_candidates(camera_id, type_key, query, top_k):
    """One search for one conflict type on one camera."""
    found = client.search(
        query,
        top_k=top_k,
        min_similarity=MIN_SIMILARITY,
        llm_top_n=0,
        metadata_filters={"camera_id": camera_id},
    )
    return found.get("results", [])


def _candidate(hit, camera_id, ctype):
    source = hit["source"]
    return {
        "id": hashlib.sha1(source.encode()).hexdigest()[:8],
        "source": source,
        "original_video": _first(hit, "original_video", "parent_video"),
        "camera_id": _first(hit, "camera_id", default=camera_id),
        "location": _first(hit, "location"),
        "view": view_of(camera_id),
        "start_sec": _first(hit, "start_sec", "segment_start_sec", "start_time", "segment_start"),
        "end_sec": _first(hit, "end_sec", "segment_end_sec", "end_time", "segment_end"),
        "similarity": round(float(hit.get("similarity_score") or 0), 3),
        "caption": hit.get("reasoning_content") or "",
        "type": {"key": ctype["key"], "label": ctype["label"]},
        "_hit": hit,
    }


def collect_candidates(cameras, top_k):
    """Search every camera for every conflict type and keep each clip once, under its best match."""
    searches = [
        (camera, ctype, query)
        for camera in cameras
        for ctype in taxonomy.CONFLICT_TYPES
        for query in ctype["queries"][view_of(camera)]
    ]
    best, last_error = {}, None
    with llm.ThreadPoolExecutor(max_workers=4) as pool:
        pending = [
            (camera, ctype, pool.submit(find_candidates, camera, ctype["key"], query, top_k))
            for camera, ctype, query in searches
        ]
        failed = 0
        for camera, ctype, future in pending:
            try:
                hits = future.result()
            except Exception as exc:
                failed += 1
                last_error = redact(str(exc))
                log.warning("search failed on %s (%s): %s", camera, ctype["key"], last_error)
                continue
            for hit in hits:
                if not hit.get("source"):
                    continue
                candidate = _candidate(hit, camera, ctype)
                kept = best.get(candidate["source"])
                if kept is None or candidate["similarity"] > kept["similarity"]:
                    best[candidate["source"]] = candidate
    if searches and failed == len(searches):
        raise RuntimeError(f"every search failed: {last_error}")
    ranked = sorted(best.values(), key=lambda c: c["similarity"], reverse=True)
    return ranked[:MAX_CANDIDATES]


# ---- 2. signals ----

_VERDICT_PATTERNS = (
    re.compile(r"(?i:VZ_VERDICT)\s*[:=]\s*\(?([A-D])\)?(?![A-Za-z])(?!\s*(?:,|or)\s*[B-D]\b)"),
    re.compile(r"(?i:\bVERDICT)\s*[:=]\s*\(?([A-D])\)?(?![A-Za-z])(?!\s*(?:,|or)\s*[B-D]\b)"),
)


# The CAUSE line, skipping the option list when a model echoes the prompt back.
_CAUSE_PATTERN = re.compile(r"(?i:\bCAUSE)\s*[:=]\s*\(?([a-z_]+)\)?(?!\s*,)")


def stored_signal(caption):
    """The verdict letter and physical cause Cosmos wrote into the description at re-ingest, if any."""
    causes = [c.lower() for c in _CAUSE_PATTERN.findall(caption or "") if c.lower() in taxonomy.CAUSES]
    cause = causes[-1] if causes else None
    for pattern in _VERDICT_PATTERNS:
        letters = pattern.findall(caption or "")
        if letters:
            return {"letter": letters[-1], "cause": cause}
    return {"letter": None, "cause": cause}


_LABEL_KEYS = ("label", "class_name", "class", "name", "category")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _box(node):
    """(x1, y1, x2, y2) from the common box encodings, or None."""
    for key in ("bbox", "box", "xyxy", "bbox_xyxy"):
        value = node.get(key)
        if isinstance(value, dict):
            return _box(value)
        if isinstance(value, (list, tuple)) and len(value) == 4 and all(map(_number, value)):
            x1, y1, x2, y2 = value
            # [x, y, w, h] when the last pair cannot be a bottom-right corner
            return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else (x1, y1, x1 + x2, y1 + y2)
    if all(_number(node.get(k)) for k in ("x1", "y1", "x2", "y2")):
        return tuple(node[k] for k in ("x1", "y1", "x2", "y2"))
    width, height = node.get("w", node.get("width")), node.get("h", node.get("height"))
    if all(map(_number, (node.get("x"), node.get("y"), width, height))):
        return (node["x"], node["y"], node["x"] + width, node["y"] + height)
    return None


def _detection(node):
    if not isinstance(node, dict):
        return None
    label = next((node[k] for k in _LABEL_KEYS if isinstance(node.get(k), str)), None)
    return {"label": label.lower(), "box": _box(node)} if label else None


def _walk(node, frames, size, labels):
    """Pull detections out of a detector payload without assuming its exact shape.

    A list whose items look like detections is one frame. Class summaries
    (object_classes / object_counts) and the frame size are picked up wherever they sit.
    """
    if isinstance(node, list):
        detections = [d for d in map(_detection, node) if d]
        if detections:
            frames.append(detections)
        else:
            for item in node:
                _walk(item, frames, size, labels)
        return
    if not isinstance(node, dict):
        return
    if _detection(node) is None and "x" not in node and "x1" not in node:
        width = _first(node, "width", "frame_width", "image_width")
        height = _first(node, "height", "frame_height", "image_height")
        if _number(width) and _number(height) and width > 1 and height > 1:
            size.setdefault("h", height)
    for key, value in node.items():
        if isinstance(value, str) and value[:1] in ("{", "["):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if key in ("object_classes", "classes") and isinstance(value, list):
            labels.update(str(v).lower() for v in value)
        elif key in ("object_counts", "class_counts") and isinstance(value, dict):
            labels.update(str(k).lower() for k, n in value.items() if n)
        else:
            _walk(value, frames, size, labels)


def _closeness(frames, size, view):
    """0..1, higher is closer.

    Dashcam: how tall the nearest person is in the frame, because the vehicle that matters
    is the one holding the camera. Fixed camera: how small the gap between a person and a
    vehicle is, measured in person-heights.
    """
    boxes = [d["box"] for frame in frames for d in frame if d["box"]]
    if not boxes:
        return None
    frame_height = 1.0 if max(max(b) for b in boxes) <= 1.5 else size.get("h")
    best = None
    for frame in frames:
        people = [d["box"] for d in frame if d["label"] in PEOPLE and d["box"]]
        if view == "dashcam":
            if not frame_height:
                return None
            for _x1, y1, _x2, y2 in people:
                best = max(best or 0, min(1.0, (y2 - y1) / frame_height))
            continue
        vehicles = [d["box"] for d in frame if d["label"] in VEHICLES and d["box"]]
        for px1, py1, px2, py2 in people:
            height = max(py2 - py1, 1e-6)
            for vx1, vy1, vx2, vy2 in vehicles:
                gap = math.hypot(
                    max(0, max(px1, vx1) - min(px2, vx2)),
                    max(0, max(py1, vy1) - min(py2, vy2)),
                )
                best = max(best or 0, 1 / (1 + gap / height))
    return None if best is None else round(best, 2)


def yolo_signal(detections, view, hit=None):
    """Does the object detector back the description up?

    Dashcam clips need a person. Fixed-camera clips need a person and a vehicle.
    ok is None when there is no detector data, which is not treated as a rejection.
    """
    frames, size, labels = [], {}, set()
    _walk(detections, frames, size, labels)
    if hit:
        _walk(hit, [], {}, labels)  # some indexes carry the detector's class summary on the hit
    for frame in frames:
        labels.update(d["label"] for d in frame)
    if not labels:
        return {"ok": None, "person": False, "vehicle": False, "closeness": None, "note": "no detector data"}

    person, vehicle = bool(labels & PEOPLE), bool(labels & VEHICLES)
    ok = person if view == "dashcam" else person and vehicle
    if not person:
        note = "detector saw no person in the clip"
    elif not ok:
        note = "detector saw no vehicle in the clip"
    elif frames:
        people = max(sum(d["label"] in PEOPLE for d in frame) for frame in frames)
        vehicles = max(sum(d["label"] in VEHICLES for d in frame) for frame in frames)
        note = f"{people} {'person' if people == 1 else 'people'}, {vehicles} vehicle{'' if vehicles == 1 else 's'}"
    else:
        note = "person and vehicle detected" if vehicle else "person detected"
    return {
        "ok": ok,
        "person": person,
        "vehicle": vehicle,
        "closeness": _closeness(frames, size, view) if ok else None,
        "note": note,
    }


def check_clip(candidate):
    """Stored verdict and detector signal for one candidate."""
    detections = None
    try:
        detections = client.detections(candidate["source"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:  # 404 just means no sidecar for this clip
            log.warning("detections failed: HTTP %s", exc.response.status_code)
    except Exception as exc:
        log.warning("detections failed: %s", redact(str(exc)))
    return stored_signal(candidate["caption"]), yolo_signal(detections, candidate["view"], candidate.get("_hit"))


# ---- 3. decide and grade ----


def decide(stored, yolo, cosmos=None, graded=None):
    """Combine the signals into (status, reject_reason). Deterministic: no model call here.

    A live Cosmos second look outranks everything. Otherwise the detector can veto, the
    verdict saved at re-ingest decides, and clips that were never re-ingested fall back
    to the grader's reading of the description.
    """
    letter = (cosmos or {}).get("letter")
    if letter:
        if letter in ("A", "B"):
            return "verified", None
        return "rejected", f"Cosmos second look: {taxonomy.VERDICTS[letter]}"
    if yolo.get("ok") is False:
        note = yolo.get("note") or "detector disagreed"
        return "rejected", note[0].upper() + note[1:]
    letter = stored.get("letter")
    if letter:
        if letter in ("C", "D"):
            return "rejected", f"Cosmos verdict at ingest: {taxonomy.VERDICTS[letter]}"
        return ("verified", None) if yolo.get("ok") else ("unverified", None)
    conflict = (graded or {}).get("conflict")
    if conflict is False:
        return "rejected", "Grader read the description as not a conflict"
    if conflict and yolo.get("ok"):
        return "verified", None
    return "unverified", None


@llm.op
def grade(caption, conflict_type, stored_letter, detector_note):
    """Severity 0-3 and a one-line reason, from the description and the other signals."""
    user = (
        f"Conflict type searched for: {conflict_type}\n"
        f"Saved verdict letter: {stored_letter or 'missing'}\n"
        f"Detector: {detector_note}\n\nDescription:\n{caption}"
    )
    reply = llm.chat_json(
        [{"role": "system", "content": taxonomy.GRADE_PROMPT}, {"role": "user", "content": user}]
    )
    try:
        severity = min(3, max(0, int(reply.get("severity", 0))))
    except (TypeError, ValueError):
        severity = 0
    return {
        "conflict": bool(reply.get("conflict")),
        "severity": severity,
        "reason": str(reply.get("reason") or "").strip()[:200],
    }


_LETTER_GRADES = {
    "A": (True, 3, "Saved verdict: near-miss or contact."),
    "B": (True, 2, "Saved verdict: conflict."),
    "C": (False, 0, "Saved verdict: normal yielding."),
    "D": (False, 0, "Saved verdict: no interaction."),
}


def _grade_or_fallback(candidate, stored, yolo):
    if llm.configured():
        try:
            return grade(candidate["caption"][:2000], candidate["type"]["label"], stored["letter"], yolo["note"])
        except Exception as exc:
            log.warning("grade failed, using the saved verdict: %s", type(exc).__name__)
    if stored["letter"]:
        conflict, severity, reason = _LETTER_GRADES[stored["letter"]]
        return {"conflict": conflict, "severity": severity, "reason": reason}
    return {"conflict": None, "severity": 1, "reason": "No grader available; needs review."}


def _assess(candidate):
    """Run every check on one candidate and return it as an API Conflict."""
    stored, yolo = check_clip(candidate)
    status, reject_reason = decide(stored, yolo)
    if status == "rejected":  # no need to spend a model call on a clip the checks already dropped
        severity = 0
        reason = (
            "No pedestrian or no vehicle was detected, so there is no pedestrian-vehicle conflict."
            if yolo.get("ok") is False
            else _LETTER_GRADES[stored["letter"]][2]
        )
    else:
        graded = _grade_or_fallback(candidate, stored, yolo)
        status, reject_reason = decide(stored, yolo, graded=graded)
        reason = graded["reason"]
        severity = graded["severity"]
        if status == "rejected":
            severity = 0
        elif status == "verified":
            severity = max(1, severity)
    return _conflict(candidate, stored, yolo, None, status, reject_reason, severity, reason)


def _conflict(candidate, stored, yolo, cosmos, status, reject_reason, severity, reason):
    fixed = candidate["view"] == "fixed"
    return {
        "id": candidate["id"],
        "source": candidate["source"],
        "original_video": candidate["original_video"],
        "camera_id": candidate["camera_id"],
        "location": candidate["location"],
        "view": candidate["view"],
        "start_sec": candidate["start_sec"],
        "end_sec": candidate["end_sec"],
        "similarity": candidate["similarity"],
        "caption": candidate["caption"][:CAPTION_LIMIT],
        "type": candidate["type"],
        "signals": {"stored": stored, "yolo": yolo, "cosmos": cosmos},
        "status": status,
        "reject_reason": reject_reason,
        "severity": severity,
        "reason": reason,
        "hotspot_key": candidate["camera_id"] if fixed else candidate["original_video"] or candidate["camera_id"],
    }


# ---- 4. rank ----


def _hotspot_label(key, conflict):
    if conflict["view"] == "fixed":
        return f"Street camera {key}"
    if key == conflict["camera_id"]:  # the index gave no parent video, so the whole dashcam is one group
        return f"Dashcam {key}"
    return f"Drive {str(key).rsplit('/', 1)[-1]}"


def rank(conflicts):
    """Group verified conflicts into hotspots: one per fixed camera, one per dashcam drive."""
    hotspots = {}
    for conflict in conflicts:
        if conflict["status"] != "verified":
            continue
        key = conflict["hotspot_key"]
        fixed = conflict["view"] == "fixed"
        spot = hotspots.setdefault(
            key,
            {
                "key": key,
                "label": _hotspot_label(key, conflict),
                "kind": "camera" if fixed else "drive",
                "camera_id": conflict["camera_id"],
                "score": 0,
                "verified": 0,
                "by_type": {},
                "conflict_ids": [],
            },
        )
        spot["score"] += conflict["severity"]
        spot["verified"] += 1
        type_key = conflict["type"]["key"]
        spot["by_type"][type_key] = spot["by_type"].get(type_key, 0) + 1
        spot["conflict_ids"].append(conflict["id"])
    return sorted(hotspots.values(), key=lambda s: (s["score"], s["verified"]), reverse=True)


def summarize(conflicts, cameras, mode="live"):
    """Order the conflicts and wrap them into the API Sweep shape."""
    conflicts = sorted(conflicts, key=lambda c: (STATUS_ORDER[c["status"]], -c["severity"], -c["similarity"]))
    counts = {status: sum(c["status"] == status for c in conflicts) for status in STATUS_ORDER}
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "cameras": cameras,
        "funnel": {"candidates": len(conflicts), **counts},
        "hotspots": rank(conflicts),
        "conflicts": conflicts,
    }


# ---- the sweep ----


@llm.op
def run_sweep(cameras=None, top_k=10, job_id=None):
    if not client.configured:
        raise RuntimeError("VSS is not configured: set VSS_URL/VSS_USERNAME/VSS_PASSWORD")
    client.login()  # once, before the worker threads start
    cameras = cameras or discover_cameras()
    _step(job_id, f"Sweeping {len(cameras)} camera(s) for {len(taxonomy.CONFLICT_TYPES)} conflict types")
    candidates = collect_candidates(cameras, top_k)
    _step(job_id, f"Search found {len(candidates)} candidate clips")
    _step(job_id, "Checking each clip: saved Cosmos verdict, object detector, severity grade")
    with llm.ThreadPoolExecutor(max_workers=8) as pool:
        conflicts = list(pool.map(_assess, candidates))
    result = summarize(conflicts, cameras)
    funnel = result["funnel"]
    _step(
        job_id,
        f"Verified {funnel['verified']}, filtered {funnel['rejected']} false alarms, "
        f"{funnel['unverified']} need review; ranked {len(result['hotspots'])} hotspot(s)",
    )
    return result


# ---- jobs, snapshots ----


def remember(result):
    """Index a sweep's conflicts by id so work orders and second looks can find them."""
    if result:
        _conflicts.update({c["id"]: c for c in result.get("conflicts", [])})
    return result


def get_conflict(conflict_id):
    if conflict_id not in _conflicts:
        for name in SNAPSHOTS:
            load_snapshot(name)
    return _conflicts.get(conflict_id)


def load_snapshot(name):
    path = SNAPSHOTS.get(name)
    if path is None or not path.exists():
        return None
    return remember(json.loads(path.read_text(encoding="utf-8")))


def save_snapshot(result, path):
    Path(path).write_text(json.dumps(result, indent=1), encoding="utf-8")


def start_job(cameras=None, top_k=10):
    job_id = uuid.uuid4().hex[:8]
    job = {"job_id": job_id, "status": "running", "steps": [], "_started": time.monotonic()}
    with _lock:
        for old in list(_jobs)[:-9]:  # keep the last ten jobs
            del _jobs[old]
        _jobs[job_id] = job
        _latest["job_id"] = job_id
    if fixture_mode():
        _step(job_id, "Fixture mode: replaying a saved sweep, no network calls")
        job["result"] = load_snapshot("after") or load_snapshot("sample")
        job["status"] = "done"
    else:
        threading.Thread(target=_run_job, args=(job, cameras, top_k), daemon=True).start()
    return job_id


def _run_job(job, cameras, top_k):
    try:
        job["result"] = remember(run_sweep(cameras, top_k, job["job_id"]))
        job["status"] = "done"
    except Exception as exc:
        log.exception("sweep %s failed", job["job_id"])
        job["error"] = redact(str(exc))[:300]
        job["status"] = "error"


def get_job(job_id):
    if job_id == "latest":
        job_id = _latest["job_id"]
    job = _jobs.get(job_id)
    return None if job is None else {k: v for k, v in job.items() if not k.startswith("_")}


# ---- extras: second look, publish, watch ----


@llm.op
def second_look_trace(source, conflict_type, letter, trace):
    """Records the live Cosmos second look in the trace (the clip bytes are not logged)."""
    return {"letter": letter, "trace": trace}


def second_look(conflict_id):
    """Send the actual clip back to Cosmos3-Reason with a direct question and re-decide."""
    conflict = get_conflict(conflict_id)
    if conflict is None:
        return None
    clip = client.segment_bytes(conflict["source"])
    if clip is None:
        raise RuntimeError("clip is missing or too large for a second look")
    ctype = taxonomy.TYPE_BY_KEY.get(conflict["type"]["key"], {})
    question = taxonomy.SECOND_LOOK_PROMPT.format(question=ctype.get("question", ""))
    trace, letter = gpu.cosmos_verify(clip, question)
    second_look_trace(conflict["source"], conflict["type"]["label"], letter, trace)
    was = conflict["status"]
    signals = conflict["signals"]
    signals["cosmos"] = {"letter": letter, "trace": trace}
    conflict["status"], conflict["reject_reason"] = decide(signals["stored"], signals["yolo"], signals["cosmos"])
    if conflict["status"] == "rejected":
        conflict["severity"] = 0
    elif conflict["status"] == "verified":
        conflict["severity"] = max(conflict["severity"], 3 if letter == "A" else 2)
    return {
        "letter": letter,
        "trace": trace,
        "agrees": letter is not None and (letter in ("A", "B")) == (was == "verified"),
        "status": conflict["status"],
        "conflict": conflict,
    }


def publish(conflict_ids, labels=None):
    """Save reviewed conflicts to W&B Weave as a dataset. Returns {url, rows, precision}."""
    import weave

    labels = labels or {}
    rows = [
        {
            "id": c["id"],
            "source": c["source"],
            "camera_id": c["camera_id"],
            "conflict_type": c["type"]["key"],
            "status": c["status"],
            "severity": c["severity"],
            "reason": c["reason"],
            "stored_verdict": c["signals"]["stored"]["letter"],
            "detector_ok": c["signals"]["yolo"]["ok"],
            "caption": c["caption"],
            "human_label": labels.get(c["id"]),
        }
        for c in map(get_conflict, conflict_ids)
        if c
    ]
    if not rows:
        return None
    ref = weave.publish(weave.Dataset(name="viz-agent-conflicts", rows=rows))
    judged = [r["human_label"] for r in rows if r["human_label"] is not None]
    try:
        url = f"https://wandb.ai/{ref.entity}/{ref.project}/weave/objects/{ref.name}/versions/{ref.digest}"
    except AttributeError:
        url = str(ref.uri()) if hasattr(ref, "uri") else str(ref)
    return {
        "url": url,
        "rows": len(rows),
        "precision": round(sum(judged) / len(judged), 2) if judged else None,
    }


def _watch_pass():
    """Assess only clips not seen before; verified ones become alerts."""
    fresh = [c for c in collect_candidates(discover_cameras(), 10) if c["id"] not in _watch["seen"]]
    with llm.ThreadPoolExecutor(max_workers=8) as pool:
        conflicts = list(pool.map(_assess, fresh))
    _watch["seen"].update(c["id"] for c in conflicts)
    remember({"conflicts": conflicts})
    return [c for c in conflicts if c["status"] == "verified"]


def _watch_loop():
    first = not _watch["seen"]
    while _watch["on"]:
        try:
            alerts = _watch_pass()
            if not first:  # the first pass only learns what is already in the index
                _watch["alerts"].extend(alerts)
            first = False
        except Exception as exc:
            log.warning("watch pass failed: %s", redact(str(exc)))
        for _ in range(WATCH_SECONDS):
            if not _watch["on"]:
                return
            time.sleep(1)


def set_watch(on):
    """Turn the watcher on or off. While on, newly indexed clips are checked as they appear."""
    with _lock:
        if on and not _watch["on"]:
            _watch["on"] = True
            _watch["seen"].update(_conflicts)  # clips from earlier sweeps are not news
            threading.Thread(target=_watch_loop, daemon=True).start()
        elif not on:
            _watch["on"] = False
    return _watch["on"]


def alerts_since(cursor):
    alerts = _watch["alerts"]
    return {"alerts": alerts[max(0, cursor) :], "cursor": len(alerts), "on": _watch["on"]}


def main():
    parser = argparse.ArgumentParser(description="Run one sweep and save it as a snapshot.")
    parser.add_argument("--out", required=True, help="e.g. app/snap_before.json or app/snap_after.json")
    parser.add_argument("--cameras", help="comma-separated camera ids; default: every pie_cam* and sf_streets_cam*")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    llm.init_tracing()
    result = run_sweep(args.cameras.split(",") if args.cameras else None, args.top_k)
    save_snapshot(result, args.out)
    print(json.dumps(result["funnel"]), "->", args.out)


if __name__ == "__main__":
    main()
