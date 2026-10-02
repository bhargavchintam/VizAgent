"""Replay a saved ViZ Agent sweep into your own W&B Weave project, so you can browse it.

The live app logs to the organizers' W&B team (vastdata/team-20), which only its members can
open. This script logs a saved run into a project in YOUR account instead: a trace tree of the
sweep (one child per clip), a dataset of every clip, and an evaluation of the funnel.

It makes no VSS, Cosmos or LLM calls; every trace is labelled as a replay of the saved run.

    export WANDB_API_KEY=...            # your own key, typed in your own terminal
    .venv/bin/python scripts/replay_to_weave.py runs/live_run.json --project vizagent
    .venv/bin/python scripts/replay_to_weave.py runs/live_run.json --dry-run   # no login needed

The input is either a saved job (GET /api/sweep/{id}, with "steps" and "result") or a snapshot
(GET /api/snapshot/{name}).
"""

import argparse
import json
import sys
from pathlib import Path


def load_run(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = data.get("result") or data
    return data.get("steps") or [], result


def row(conflict):
    signals = conflict.get("signals") or {}
    return {
        "id": conflict["id"],
        "camera_id": conflict.get("camera_id"),
        "view": conflict.get("view"),
        "start_sec": conflict.get("start_sec"),
        "type": (conflict.get("type") or {}).get("label"),
        "status": conflict.get("status"),
        "reject_reason": conflict.get("reject_reason") or "",
        "severity": conflict.get("severity", 0),
        "reason": conflict.get("reason") or "",
        "detector": (signals.get("yolo") or {}).get("note") or "",
        "verdict_at_ingest": (signals.get("stored") or {}).get("letter") or "",
        "caption": (conflict.get("caption") or "")[:600],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="saved job or snapshot JSON")
    parser.add_argument("--project", default="vizagent", help="Weave project in your account")
    parser.add_argument("--dry-run", action="store_true", help="print what would be logged; no login")
    args = parser.parse_args()

    steps, result = load_run(args.run)
    rows = [row(c) for c in result.get("conflicts", [])]
    funnel = result.get("funnel", {})
    source = {"replayed_from": result.get("weave_url") or "saved snapshot", "generated_at": result.get("generated_at")}
    print(f"run {source['generated_at']}: funnel {funnel}, {len(rows)} clips, {len(steps)} steps")
    if args.dry_run:
        for step in steps:
            print(f"  [{step['t']}s] {step['text']}")
        return

    import weave

    weave.init(args.project)

    @weave.op
    def replay_check(clip):
        """One clip as the live sweep decided it (recorded, not recomputed)."""
        return {k: clip[k] for k in ("status", "reject_reason", "severity", "reason", "detector")}

    @weave.op
    def replay_sweep(cameras, steps, replayed_from, generated_at):
        """The saved ViZ Agent sweep, replayed: one replay_check per clip, then the funnel."""
        for clip in rows:
            replay_check(clip)
        return {"funnel": funnel, "hotspots": result.get("hotspots", [])}

    with weave.attributes({"replay": True, **source}):
        replay_sweep(result.get("cameras", []), [s["text"] for s in steps], **source)

    ref = weave.publish(weave.Dataset(name="vizagent-live-run", rows=rows))
    ev = weave.EvaluationLogger(name="vizagent-funnel-replay", model="vizagent-sweep", dataset="vizagent-live-run")
    for clip in rows:
        prediction = ev.log_prediction(inputs={"id": clip["id"], "camera_id": clip["camera_id"]}, output=clip["status"])
        prediction.log_score("verified", clip["status"] == "verified")
        prediction.finish()
    ev.log_summary({**funnel, "replay": True})
    print(f"Dataset: {ref.uri()}")
    print("Open your Weave project in the browser: Traces, Datasets and Evaluations.")


if __name__ == "__main__":
    sys.exit(main())
