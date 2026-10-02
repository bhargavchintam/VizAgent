"""Run the VM side of the build in one go.

On the workshop VM, from the repo root:

    git pull && python scripts/vm_run.py           # every step; asks once before the re-ingest
    python scripts/vm_run.py probe before          # or only the steps you name
    python scripts/vm_run.py --yes                 # no question asked

Steps, in order:
    probe      print the shape of real VSS responses
    before     save app/snap_before.json (never overwritten once saved)
    reingest   re-ingest the chunks with the most conflict candidates, using our prompt, and wait
    after      save app/snap_after.json
    deploy     deploy to /app and give the pod the Cosmos endpoint for the second look

Steps are safe to re-run. Everything printed is also saved to vm_run_log.txt with bucket
names, hosts and tokens masked, so the file can be pasted back to whoever builds the engine.
"""

import glob
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "scripts"))

LOG = ROOT / "vm_run_log.txt"
STATE = ROOT / ".vm_run_state.json"
RAW_BEFORE = ROOT / "raw_before.json"
STEPS = ("probe", "before", "reingest", "after", "deploy")
FINISHED = ("completed", "complete", "done", "failed", "error")
COSMOS_DEFAULT = "http://166.19.38.112:8001"  # the address in the organizers' gpu skills


def parse_config(text):
    """KEY=VALUE lines of a team config file, without comments or quotes."""
    values = {}
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split(" #", 1)[0].strip().strip("'\"")
    return values


def load_team_config():
    """Fill in anything the VM environment did not already export. Values are never printed."""
    configs = sorted(glob.glob("/config/*.config"))
    if len(configs) == 1:
        for key, value in parse_config(Path(configs[0]).read_text()).items():
            os.environ.setdefault(key, value)
    if "KUBECONFIG" not in os.environ:
        for candidate in ["/config/kubeconfig", *sorted(glob.glob("/config/*-k8s.yaml"))]:
            if Path(candidate).exists():
                os.environ["KUBECONFIG"] = candidate
                break


def say(text=""):
    from probe import mask

    text = mask(str(text))
    print(text, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def name_of(video):
    return str(video).rsplit("/", 1)[-1]


def read_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"reingested": []}


# ---- steps ----


def step_probe(_args):
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "probe.py")], capture_output=True, text=True, check=False
    )
    say(out.stdout + out.stderr[-2000:])


def dump_raw_before():
    """The plain search results as they are now, in case the sweep itself fails before a re-ingest."""
    import sweep
    import taxonomy

    dump = []
    for camera in sweep.discover_cameras():
        for ctype in taxonomy.CONFLICT_TYPES:
            for query in ctype["queries"][sweep.view_of(camera)]:
                found = sweep.client.search(
                    query, top_k=15, min_similarity=sweep.MIN_SIMILARITY, llm_top_n=0,
                    metadata_filters={"camera_id": camera},
                )  # fmt: skip
                dump.append({"camera": camera, "type": ctype["key"], "query": query, "results": found.get("results", [])})
    RAW_BEFORE.write_text(json.dumps(dump, indent=1), encoding="utf-8")
    return sum(len(d["results"]) for d in dump)


def step_before(_args):
    import sweep

    path = sweep.SNAPSHOTS["before"]
    if path.exists():
        say(f"before: already saved, keeping it: {json.loads(path.read_text())['funnel']}")
        return
    if not RAW_BEFORE.exists():
        say(f"before: saved {dump_raw_before()} raw search hits to {RAW_BEFORE.name}")
    say("before: running the sweep (this can take a minute)")
    try:
        result = sweep.run_sweep()
    except Exception as exc:
        say(f"before: THE SWEEP FAILED ({type(exc).__name__}: {exc}). Paste this back.")
        say("before: the raw search hits are saved, so the re-ingest can still go ahead.")
        return
    sweep.save_snapshot(result, path)
    say(f"before: cameras {result['cameras']}, funnel {result['funnel']}, {len(result['hotspots'])} hotspot(s)")
    for conflict in result["conflicts"][:3]:
        say(f"  e.g. [{conflict['status']}] {conflict['type']['key']}: {conflict['caption'][:160]}")


def pick_chunks(per_dashcam=2, max_fixed=2):
    """The parent videos with the most conflict candidates: (camera, original_video, score)."""
    import sweep
    import taxonomy

    picks, fixed_left = [], max_fixed
    for camera in sweep.discover_cameras():
        view, scores = sweep.view_of(camera), {}
        for ctype in taxonomy.CONFLICT_TYPES:
            for query in ctype["queries"][view]:
                found = sweep.client.search(
                    query, top_k=30, min_similarity=sweep.MIN_SIMILARITY, llm_top_n=0,
                    metadata_filters={"camera_id": camera},
                )  # fmt: skip
                chunks = found.get("chunk_results") or []
                for chunk in chunks:
                    if chunk.get("original_video"):
                        video = chunk["original_video"]
                        scores[video] = scores.get(video, 0) + (chunk.get("matched_segment_count") or 1)
                if not chunks:
                    for hit in found.get("results", []):
                        if hit.get("original_video"):
                            scores[hit["original_video"]] = scores.get(hit["original_video"], 0) + 1
        want = per_dashcam if view == "dashcam" else min(1, fixed_left)
        top = sorted(scores, key=scores.get, reverse=True)[:want]
        if view == "fixed":
            fixed_left -= len(top)
        picks += [(camera, video, scores[video]) for video in top]
    return picks


def step_reingest(args):
    import httpx

    import sweep
    import taxonomy

    if not (sweep.SNAPSHOTS["before"].exists() or RAW_BEFORE.exists()):
        say("reingest: skipped, because nothing from before is saved yet. Run the 'before' step first.")
        return
    state = read_state()
    picks = [p for p in pick_chunks() if p[1] not in state["reingested"]]
    if not picks:
        say("reingest: nothing to do (no candidates, or the chosen chunks were already re-ingested)")
        return
    say("reingest: chunks with the most conflict candidates:")
    for camera, video, score in picks:
        say(f"  {camera}: {name_of(video)} ({score} matching clips)")
    if not args.yes:
        if not sys.stdin.isatty():
            say("reingest: skipped, no terminal to ask on. Re-run with --yes to go ahead.")
            return
        prompt = f"Re-ingest these {len(picks)} chunk(s) with the ViZ prompt? It overwrites their descriptions. [y/N] "
        if input(prompt).strip().lower() != "y":
            say("reingest: skipped at your request")
            return

    jobs = {}
    for _camera, video, _score in picks:
        body = {"original_video": video, "chunk_count": 1, "custom_prompt": taxonomy.REINGEST_PROMPT}
        try:
            job = sweep.client._request("POST", "dashboard/reingest", json=body)
            jobs[job["job_id"]] = video
            say(f"reingest: started {name_of(video)}: {job.get('copied_segments', '?')} clips")
        except Exception as exc:
            detail = exc.response.text[:300] if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            say(f"reingest: could not start {name_of(video)} ({type(exc).__name__}: {detail})")

    started, last = time.monotonic(), {}
    while jobs and time.monotonic() - started < args.wait_minutes * 60:
        time.sleep(5)
        for job_id, video in list(jobs.items()):
            try:
                status = sweep.client._request("GET", f"dashboard/reingest/{job_id}")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:  # the backend restarted and lost the progress record
                    say(f"reingest: progress record for {name_of(video)} is gone; it may still be running")
                    del jobs[job_id]
                continue
            except httpx.HTTPError:
                continue
            phase = str(status.get("status", "")).lower()
            line = (
                f"reingest: {name_of(video)}: {status.get('completed_chunks', '?')}/{status.get('total_chunks', '?')} chunks, "
                f"{status.get('indexed_segments', '?')}/{status.get('total_segments', '?')} clips, {phase or 'running'}"
            )
            if last.get(job_id) != line:
                last[job_id] = line
                say(line)
            if phase in FINISHED:
                del jobs[job_id]
                if phase in FINISHED[:3]:
                    state["reingested"].append(video)
                    STATE.write_text(json.dumps(state))
    if jobs:
        say(f"reingest: still running after {args.wait_minutes} minutes: {[name_of(v) for v in jobs.values()]}")


def step_after(_args):
    import sweep

    say("after: running the sweep (this can take a minute)")
    try:
        result = sweep.run_sweep()
    except Exception as exc:
        say(f"after: THE SWEEP FAILED ({type(exc).__name__}: {exc}). Paste this back.")
        return
    sweep.save_snapshot(result, sweep.SNAPSHOTS["after"])
    before = sweep.SNAPSHOTS["before"]
    if before.exists():
        say(f"after: before {json.loads(before.read_text())['funnel']}")
    say(f"after: now    {result['funnel']}, {len(result['hotspots'])} hotspot(s)")
    letters = [c["signals"]["stored"]["letter"] for c in result["conflicts"]]
    say(f"after: saved verdicts found: { {x: letters.count(x) for x in sorted(set(letters), key=str)} }")
    for conflict in result["conflicts"][:3]:
        say(f"  e.g. [{conflict['status']}] sev {conflict['severity']} {conflict['type']['key']}: {conflict['reason']}")


def step_deploy(_args):
    import llm

    env = dict(os.environ)
    env.setdefault("LLM_MODEL", llm.DEFAULT_MODEL)
    done = subprocess.run(["bash", str(ROOT / "deploy" / "deploy.sh")], env=env, capture_output=True, text=True, check=False)
    say(done.stdout[-3000:] + done.stderr[-1500:])
    if done.returncode != 0:
        say(f"deploy: deploy.sh exited with {done.returncode}. Paste this back.")
        return

    # The second look needs the Cosmos endpoint inside the pod; deploy.sh does not pass it.
    namespace = os.environ.get("USERNAME", "")
    literals = [f"--from-literal=COSMOS3_REASON_URL={os.environ.get('COSMOS3_REASON_URL') or COSMOS_DEFAULT}"]
    if os.environ.get("GPU_BEARER_TOKEN"):
        literals.append(f"--from-literal=GPU_BEARER_TOKEN={os.environ['GPU_BEARER_TOKEN']}")
    try:
        manifest = subprocess.run(
            ["kubectl", "-n", namespace, "create", "secret", "generic", "vizagent-gpu", *literals, "--dry-run=client", "-o", "yaml"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        subprocess.run(["kubectl", "-n", namespace, "apply", "-f", "-"], input=manifest, text=True, capture_output=True, check=True)
        subprocess.run(
            ["kubectl", "-n", namespace, "set", "env", "deploy/vizagent", "--from=secret/vizagent-gpu"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        subprocess.run(
            ["kubectl", "-n", namespace, "rollout", "status", "deploy/vizagent", "--timeout=300s"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        say("deploy: the pod now has the Cosmos endpoint for the second look")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        say(f"deploy: could not add the Cosmos endpoint ({type(exc).__name__}); the second look stays off")


RUN = {"probe": step_probe, "before": step_before, "reingest": step_reingest, "after": step_after, "deploy": step_deploy}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run the VM side of the build: " + ", ".join(STEPS))
    parser.add_argument("steps", nargs="*", help="default: all of them, in order")
    parser.add_argument("--yes", action="store_true", help="re-ingest without asking")
    parser.add_argument("--wait-minutes", type=int, default=20, help="how long to wait for the re-ingest")
    args = parser.parse_args()
    unknown = [step for step in args.steps if step not in STEPS]
    if unknown:
        parser.error(f"unknown step(s) {unknown}; choose from {', '.join(STEPS)}")

    load_team_config()
    logging.basicConfig(level=logging.WARNING)
    import llm

    llm.init_tracing()
    for step in args.steps or STEPS:
        say(f"\n########## {step} ##########")
        try:
            RUN[step](args)
        except Exception as exc:
            say(f"{step}: FAILED ({type(exc).__name__}: {exc}). Paste this back.")
    say(f"\nDone. Paste back the contents of {LOG.name}.")


if __name__ == "__main__":
    main()
