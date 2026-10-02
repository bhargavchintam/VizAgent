"""Extras that sit next to the sweep's own second look, publish and watch (in sweep.py).

- Fixture answers, so the page can show every extra offline.
- Re-ranking the latest sweep after a second look changes a conflict.
- The human review logged as a Weave evaluation (precision of the agent).
- Context: the clips just before and after a conflict.
- Re-ingest loop: propose a sharper ingestion prompt for a conflict type and re-ingest one chunk.
"""

import logging

import llm
import sweep
import taxonomy

log = logging.getLogger("vizagent.extras")

FIXTURE_TRACE = (
    "Fixture mode, no model was called. Example of what Cosmos3-Reason returns: the camera car is "
    "moving at a steady pace; a pedestrian steps off the right curb into the crosswalk; the car "
    "does not slow until the pedestrian is in its lane, and the pedestrian stops to let it pass."
)


def refresh_latest():
    """Re-rank the latest sweep after a conflict's status changed."""
    job = sweep._jobs.get(sweep._latest["job_id"])
    result = (job or {}).get("result")
    if result:
        fresh = sweep.summarize(result["conflicts"], result.get("cameras", []), result.get("mode", "live"))
        result.update({k: fresh[k] for k in ("funnel", "hotspots", "conflicts")})


# ---- fixture answers ----


def fixture_second_look(conflict):
    return {"letter": "B", "trace": FIXTURE_TRACE, "agrees": conflict["status"] == "verified",
            "status": conflict["status"], "conflict": conflict}  # fmt: skip


def _precision(conflicts, labels):
    judged = [bool(labels[c["id"]]) for c in conflicts if c["id"] in labels]
    return round(sum(judged) / len(judged), 2) if judged else None


def fixture_publish(conflicts, labels):
    return {"url": None, "rows": len(conflicts), "precision": _precision(conflicts, labels), "eval_url": None}


# ---- human review as a Weave evaluation ----


def _weave_url(path):
    project = llm.wandb_project()
    return f"https://wandb.ai/{project}/weave/{path}" if project else None


def log_review(conflicts, labels):
    """Log thumbs up/down as a Weave evaluation of the agent; returns its link, or None without labels."""
    reviewed = [c for c in conflicts if c["id"] in labels]
    if not reviewed:
        return None
    import weave

    ev = weave.EvaluationLogger(name="vizagent-human-review", model="vizagent-sweep", dataset="viz-agent-conflicts")
    for c in reviewed:
        prediction = ev.log_prediction(
            inputs={"source": c["source"], "type": c["type"]["key"], "camera_id": c["camera_id"]},
            output={"status": c["status"], "severity": c.get("severity", 0)},
        )
        prediction.log_score("human_agrees", bool(labels[c["id"]]))
        prediction.finish()
    ev.log_summary({"precision": _precision(conflicts, labels), "reviewed": len(reviewed)})
    return getattr(ev, "ui_url", None) or _weave_url("evaluations")


# ---- before / after context ----


def _segment_list(node):
    """The first list of segment dicts (each with a 'source') anywhere in the response."""
    if isinstance(node, list):
        if node and all(isinstance(s, dict) and s.get("source") for s in node):
            return node
        for item in node:
            found = _segment_list(item)
            if found:
                return found
    elif isinstance(node, dict):
        for value in node.values():
            found = _segment_list(value)
            if found:
                return found
    return []


def context(conflict):
    """The segments right before and after a conflict in the same parent video."""
    here = {"source": conflict["source"], "start_sec": conflict.get("start_sec")}
    if sweep.fixture_mode() or not conflict.get("original_video"):
        return {"prev": None, "this": here, "next": None}
    segments = _segment_list(sweep.client.segments(conflict["original_video"]))

    def start(seg):
        value = sweep._first(seg, "start_sec", "segment_start_sec", "start_time", "segment_start")
        return value if isinstance(value, (int, float)) else float("inf")

    ordered = sorted(segments, key=lambda s: (start(s), s["source"]))
    sources = [s["source"] for s in ordered]
    if conflict["source"] not in sources:
        return {"prev": None, "this": here, "next": None}

    def pick(i):
        if not 0 <= i < len(ordered):
            return None
        value = start(ordered[i])
        return {"source": ordered[i]["source"], "start_sec": None if value == float("inf") else value}

    i = sources.index(conflict["source"])
    return {"prev": pick(i - 1), "this": pick(i), "next": pick(i + 1)}


# ---- self-improving re-ingest ----

PROPOSE_PROMPT = """You improve the ingestion prompt of a video search pipeline.
Cosmos Reason writes one description per short clip using this prompt, and only what the
description mentions can be searched later. The sweep found no verified clips of this conflict type:
{label}: {question}
Rewrite the prompt so descriptions clearly state the details needed to find and judge this conflict
type. Keep it under 780 characters. Keep its last two sentences exactly as they are.
Reply with the new prompt only.

Current prompt:
{prompt}"""


def propose_prompt(type_key):
    """A sharper ingestion prompt for a conflict type the sweep could not find; falls back to the official one."""
    ctype = taxonomy.TYPE_BY_KEY.get(type_key)
    if ctype is None:
        raise ValueError(f"unknown conflict type {type_key!r}")
    official = taxonomy.REINGEST_PROMPT
    if llm.configured() and not sweep.fixture_mode():
        try:
            reply = llm.chat(
                [{"role": "user", "content": PROPOSE_PROMPT.format(prompt=official, **ctype)}], temperature=0.3
            ).strip().strip('"')
            if len(reply) <= 800 and "VERDICT: A, B, C or D" in reply and "CAUSE:" in reply:
                return {"prompt": reply, "chars": len(reply), "origin": "proposed by the agent"}
        except Exception as exc:
            log.warning("prompt proposal failed: %s", type(exc).__name__)
    return {"prompt": official, "chars": len(official), "origin": "official prompt"}


def start_reingest(original_video, prompt=None):
    prompt = prompt or taxonomy.REINGEST_PROMPT
    if len(prompt) > 800:
        raise ValueError("the re-ingest prompt must be at most 800 characters")
    return sweep.client.reingest(original_video, prompt, chunk_count=1)


def reingest_status(job_id):
    return sweep.client.reingest_status(job_id)
