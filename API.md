# ViZ Agent API contract

> **Who is building what (updated 1:58 PM Pacific, Oct 2):** `app/index.html` is being built in SripV's
> session against this contract; a first version lands on `main` by about 2:20 PM. Please don't write
> the page in parallel. Engine extras, `deploy/deploy.sh` and the VM run stay with Bhargav's session.
> The VM side runs in one command: `git pull && python scripts/vm_run.py`.

The contract between the engine (`app/*.py`) and the page (`app/index.html`).
All paths are relative to the page, because Ingress serves the app under `/app` and strips the prefix.
Build URLs with `new URL('api/…', location.href)`.

To build the page without the VM, run the app in fixture mode. It serves `app/snap_sample.json`
(or `app/snap_after.json` if one exists) and makes no network calls:

```sh
VIZ_MODE=fixture python app/main.py      # http://localhost:8080/
```

## Routes

| Route | Body | Returns |
|-------|------|---------|
| `GET /health` | | `{ok, vss_configured, llm_configured, tracing, gpu_configured, mode, features}` |
| `POST /api/sweep` | `{cameras?: [str], top_k?: int}` | `{job_id}` |
| `GET /api/sweep/{job_id}` | | `Job`. `job_id` may be `latest` |
| `GET /api/snapshot/{name}` | | `Sweep`. `name` is `before` or `after`; 404 if not saved |
| `POST /api/workorder` | `{conflict_ids: [str]}` | `WorkOrder` |
| `POST /api/workorder/send` | `{conflict_ids: [str]}` | the `WorkOrder` with `status: "SENT"`, `sent_to` (`discord`, `slack` or `webhook`), `sent_at`: posts the engineer-approved ticket to the team's work-order queue (only if `features.dispatch`) |
| `POST /api/second-look` | `{conflict_id}` | `{letter, trace, agrees, status, conflict}`; `conflict` is the updated Conflict (only if `features.cosmos`) |
| `POST /api/publish` | `{conflict_ids: [str], labels?: {id: bool}}` | `{url, rows, precision, eval_url, review}`; `review` is `{search_only: {real, total}, after_checks: {real, total}, filtered_out: {false_alarms, total}}`; `precision` is approved / labelled, or null; `eval_url` links the human review logged as a Weave evaluation, or null (only if `features.publish`) |
| `POST /api/watch` | `{on: bool}` | `{on}` (only if `features.watch`) |
| `GET /api/alerts?since=N` | | `{alerts: [Conflict], cursor: N, on}`; poll with the last `cursor` (only if `features.watch`) |
| `GET /api/context?conflict_id=` | | `{prev, this, next}`, each `{source, start_sec}` or null: the clips just before and after (only if `features.context`) |
| `POST /api/reingest/propose` | `{type: type_key}` | `{prompt, chars, origin}`: a sharper ingestion prompt for a conflict type with no verified hits (only if `features.reingest`) |
| `POST /api/reingest` | `{original_video, prompt?, confirm: true}` | backend job `{job_id, ...}`; re-ingests 1 chunk; 400 without `confirm` (only if `features.reingest`) |
| `GET /api/reingest/{job_id}` | | backend progress (`completed_chunks`, `indexed_segments`, `status`, ...) |
| `POST /api/search`, `POST /api/ask` | unchanged | unchanged (plain search, for the before/after comparison) |
| `GET /api/stream?source=` | | the clip, seekable. Use `conflict.source`, URL-encoded |

`features` is `{cosmos, publish, watch, context, reingest, dispatch}` (all bool). Show a feature's button only when its flag is true;
a route whose feature is off returns 503. In fixture mode every extra except `reingest` is on and returns a canned answer.
`mode` is `live` or `fixture`.

Re-ingest loop for the demo: start watch mode, propose a prompt for a conflict type with no hits, re-ingest one chunk,
and the newly described clips show up as alerts within a minute or two (re-ingested clips get new `source` keys).

## Shapes

```
Job = {
  job_id, status: "running" | "done" | "error",
  steps: [{t: seconds since start, text}],      // poll every 1.5 s and render as a live list
  error?: str,
  result?: Sweep                                 // present when status is "done"
}

Sweep = {
  generated_at: ISO time, mode, cameras: [str],
  weave_url,               // link to this sweep's trace in W&B Weave, or null when tracing is off
  funnel: {candidates, verified, rejected, unverified},
  hotspots: [Hotspot],     // ranked, worst first
  conflicts: [Conflict]    // every candidate: verified first, then unverified, then rejected
}

Hotspot = {
  key, label, kind: "camera" | "drive", camera_id,
  score,                   // sum of severity over verified conflicts
  verified,                // count
  by_type: {type_key: count},
  conflict_ids: [str]      // verified conflicts in this hotspot, worst first
}

Conflict = {
  id, source, original_video, camera_id, location,
  view: "dashcam" | "fixed",
  start_sec, end_sec,      // may be null
  similarity, caption,     // caption is cut to 600 characters
  type: {key, label},
  signals: {
    stored: {letter: "A" | "B" | "C" | "D" | null,    // Cosmos verdict saved at re-ingest
             cause: "blocked_view" | "turning_vehicle" | "no_crosswalk" | "did_not_slow" | null},  // physical reason
    yolo:   {ok: true | false | null, person, vehicle, closeness, note},   // ok null = no detector data
    cosmos: {letter, trace} | null                    // live second look, if it ran
  },
  status: "verified" | "rejected" | "unverified",
  reject_reason,           // why a rejected clip was dropped, else null
  severity: 0..3, reason,  // one-line reason from the grader
  hotspot_key
}

WorkOrder = {
  id, status: "DRAFT",     // drafted only, never sent anywhere
  cause,                   // most common physical cause among the approved clips, or null
  countermeasure,          // the recommended fix: chosen by cause first, else by conflict type
  expected_effect,         // FHWA crash-reduction figure for that fix, or null
  open311: {service_code, service_name, description, address_string, media_url, attribute: {...}},
  markdown                 // the same ticket as a readable brief
}
```

Verdict letters: A near-miss or contact, B conflict, C normal yielding, D no interaction.
Causes and the fix each one picks (from `app/taxonomy.py`):

| cause | first fix | expected effect |
|-------|-----------|-----------------|
| `blocked_view` | Daylighting, no parking within 20 ft (CA AB 413) | removes parked vehicles that hide people |
| `turning_vehicle` | Leading pedestrian interval | about -13% related crashes (FHWA) |
| `no_crosswalk` | Rectangular rapid flashing beacon | up to -47% pedestrian crashes (FHWA) |
| `did_not_slow` | Speed safety camera | -20 to -37% fatal and injury crashes (FHWA) |

## Deploy env (`deploy/deploy.sh`)

| Where | Name | Value |
|-------|------|-------|
| env | `LLM_MODEL` | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B` (fallback `openai/gpt-oss-20b` is built in) |
| Secret | `COSMOS3_REASON_URL`, `GPU_BEARER_TOKEN` | from `/config/<team>.config`; without the URL the second look is off |
| env (optional) | `VIZ_COSMOS`, `VIZ_PUBLISH`, `VIZ_CONTEXT` | default on; `0` turns one off |
| env (optional) | `VIZ_WATCH`, `VIZ_WATCH_SECONDS` | watch is **off** by default (each pass re-runs every search); when on, passes are `VIZ_WATCH_SECONDS` apart (default 600) |
| Secret | `DISPATCH_WEBHOOK_URL` | a Discord or Slack channel webhook for the work-order queue; set it on the VM before deploying (it is a secret: never commit it) |
| env | `VIZ_AUTO_SECOND_LOOK` | `0` by default; `1` makes Cosmos Reason re-watch the top 3 clips after each sweep (only once the pod can reach the GPU) |
| env | `VIZ_REINGEST` | `0` by default; `1` only after testing (it rewrites the team's index) |
| Ingress annotation | `nginx.ingress.kubernetes.io/proxy-read-timeout` | `"300"` (second look can take a minute) |

The official re-ingest prompt is `REINGEST_PROMPT` in `app/taxonomy.py` (774 chars, limit 800). It ends with a
`CAUSE:` line and the `VERDICT:` line; `scripts/probe.py` prints it.

## Page notes

- Keep thumbs up/down labels in `localStorage`, keyed by `conflict.id`. The pod restarts on every deploy, so anything kept server-side is lost.
- Send only approved conflict ids to `POST /api/workorder`.
- "False alarms filtered" is the list of conflicts with `status: "rejected"`; show each one's `reject_reason`.
