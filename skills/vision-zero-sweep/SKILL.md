---
name: vision-zero-sweep
description: >-
  Find verified pedestrian-vehicle conflicts in the team's indexed street and dashcam video with
  ViZ Agent, rank where they cluster, and draft a safety work order. Use when asked to "find
  conflicts", "where is it dangerous for pedestrians", "run a safety sweep", "check this clip with
  Cosmos", or "draft a work order / safety ticket" from video evidence.
---

# Vision Zero sweep (ViZ Agent)

ViZ Agent sits on top of the VSS index. For each conflict type it searches every street and dashcam
camera, checks each clip three ways (the Cosmos Reason verdict saved at ingest, the YOLO detector,
a severity grader on W&B Inference), and only keeps clips no check disagrees with. An optional
**second look** sends the clip itself back to Cosmos3-Reason and shows its reasoning.

Base URL: `http://localhost:8080` on the VM, or `$PUBLIC_URL` (the team host + `/app`) when deployed.
Never put tokens in requests; the app holds every credential itself.

## 1. Check what is on

```bash
curl -s "$BASE/health"     # features: {cosmos, publish, watch, context, reingest}
```

## 2. Run a sweep and wait for it

```bash
JOB=$(curl -s -X POST "$BASE/api/sweep" -H 'Content-Type: application/json' -d '{}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')
curl -s "$BASE/api/sweep/$JOB"    # poll every few seconds until status is "done"
```

`result.funnel` gives candidates / verified / rejected; `result.hotspots` is ranked worst first;
`result.conflicts[]` holds each clip with `signals.stored.letter` (A-D), `signals.stored.cause`,
`signals.yolo`, `status`, `reject_reason`, `severity` and `reason`.

Conflict types: `failure_to_yield`, `turning_conflict`, `midblock_crossing`, `blocked_crosswalk`.

## 3. Ask Cosmos about one clip (if `features.cosmos`)

```bash
curl -s -X POST "$BASE/api/second-look" -H 'Content-Type: application/json' -d '{"conflict_id":"<id>"}'
```

Returns `letter`, Cosmos's reasoning `trace`, and whether it `agrees` with the sweep. A disagreement
re-decides the conflict.

## 4. Draft the work order

Send only clips a person approved:

```bash
curl -s -X POST "$BASE/api/workorder" -H 'Content-Type: application/json' -d '{"conflict_ids":["<id>","<id>"]}'
```

The fix is picked by the physical **cause** Cosmos recorded (blocked view → daylighting, turning
vehicle → leading pedestrian interval, no crosswalk → flashing beacon, did not slow → speed camera),
with the FHWA crash-reduction figure in `expected_effect`. `open311` is an Open311 GeoReport v2
service request. It is a **draft**: never send it anywhere without the user's explicit OK.

## 5. Real time (if `features.watch`)

```bash
curl -s -X POST "$BASE/api/watch" -H 'Content-Type: application/json' -d '{"on":true}'
curl -s "$BASE/api/alerts?since=0"   # new verified conflicts as they are indexed; poll with `cursor`
```

## Rules

- Say "conflict", not "near-miss", unless the verdict is A.
- Never claim distances in meters, speeds, or the same person across cameras.
- Re-ingest (`/api/reingest`) rewrites the team's shared index: one chunk at a time, only with the user's OK.
