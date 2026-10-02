# ViZ Agent API contract

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
| `POST /api/second-look` | `{conflict_id}` | `{letter, trace, agrees, status, conflict}`; `conflict` is the updated Conflict (only if `features.cosmos`) |
| `POST /api/publish` | `{conflict_ids: [str], labels?: {id: bool}}` | `{url, rows, precision}`; `precision` is approved / labelled, or null (only if `features.publish`) |
| `POST /api/watch` | `{on: bool}` | `{on}` (only if `features.watch`) |
| `GET /api/alerts?since=N` | | `{alerts: [Conflict], cursor: N, on}`; poll with the last `cursor` (only if `features.watch`) |
| `POST /api/search`, `POST /api/ask` | unchanged | unchanged (plain search, for the before/after comparison) |
| `GET /api/stream?source=` | | the clip, seekable. Use `conflict.source`, URL-encoded |

`features` is `{cosmos: bool, publish: bool, watch: bool}`. Show a feature's button only when its flag is true.
`mode` is `live` or `fixture`.

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
    stored: {letter: "A" | "B" | "C" | "D" | null},   // Cosmos verdict saved at re-ingest
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
  open311: {service_code, service_name, description, address_string, media_url, attribute: {...}},
  markdown                 // the same ticket as a readable brief
}
```

Verdict letters: A near-miss or contact, B conflict, C normal yielding, D no interaction.

## Page notes

- Keep thumbs up/down labels in `localStorage`, keyed by `conflict.id`. The pod restarts on every deploy, so anything kept server-side is lost.
- Send only approved conflict ids to `POST /api/workorder`.
- "False alarms filtered" is the list of conflicts with `status: "rejected"`; show each one's `reject_reason`.
