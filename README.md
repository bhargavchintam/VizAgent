# ViZ Agent

A Vision Zero conflict finder, built at the **VAST Builders Challenge: Real-Time Video Agents Hack**
(San Francisco, Oct 2, 2026).

ViZ Agent sweeps street-camera and dashcam video for pedestrian–vehicle conflicts, double-checks every
clip, ranks where the conflicts happen, and drafts the work order a city traffic engineer could act on.

**Why it matters.** Cities usually learn a crossing is dangerous after someone is hit. San Francisco's
transport agency reported severe-injury collisions up 8% in the first half of 2026
([ABC7](https://abc7news.com/post/san-francisco-municipal-transportation-agency-report-shows-severe-injury-collisions-8-city-streets-2026/19593328/)),
and the city's 2025 Street Safety Act now requires quarterly public safety dashboards
([GrowSF](https://growsf.org/news/2025-09-26-street-safety-act/)). The video already exists. Nobody has time to watch it.

## How it works

```
1. Sweep     search every street and dashcam camera for four conflict types
2. Check     each clip must pass independent checks:
               - the verdict Cosmos Reason saved when we re-ingested the clip with our prompt
               - the object detector (YOLO): is there really a person, and a vehicle?
               - optional: a live second look, where Cosmos Reason re-watches the clip
3. Decide    a fixed rule turns the checks into verified / filtered out / needs review
4. Grade     an LLM on W&B inference scores severity 0-3 and gives a one-line reason
5. Rank      verified conflicts group into hotspots: one per fixed camera, one per dashcam drive
6. Act       draft an Open311-style work order: the fix follows the physical cause, with evidence clips
7. Prove     a person marks clips right or wrong; the score is saved to W&B Weave
```

The four conflict types: failure to yield at a crosswalk, a vehicle turning across a pedestrian's path,
a mid-block crossing in front of a moving vehicle, and a vehicle stopped on the crosswalk.

What it adds on top of plain video search:

- **False alarms are filtered, and you can see why.** Every dropped clip keeps its reason, for example "detector saw no person in the clip".
- **The ingestion prompt is ours.** Our prompt makes Cosmos Reason end each description with a `CAUSE:` and a `VERDICT:` line, so a search hit becomes a checkable claim.
- **It ends in an action.** The work order names a fix, the reason for it and the expected effect. It is a draft: nothing is sent anywhere.

## Stack

| Layer | What we use |
|-------|-------------|
| Video ingest + index | VAST AI OS: S3, DataEngine, VastDB (VSS Blueprint pipeline) |
| Video understanding | NVIDIA Cosmos3-Reason: clip descriptions at ingest, and the live second look |
| Search | NVIDIA Cosmos Embed1 hybrid search, filtered by camera |
| Object detection | YOLO11 detections per clip |
| Agent reasoning | NVIDIA Nemotron on Weights & Biases (CoreWeave) serverless inference |
| Observability and proof | W&B Weave: a trace of every sweep, the reviewed clips as a dataset, the human review as an evaluation |
| App | Python 3.12, FastAPI, one plain HTML/JS page |
| Agent Skill | `skills/vision-zero-sweep/SKILL.md`, so another agent can run a sweep and fetch work orders |

## Run it, step by step

Everything live runs on the workshop VM, where the team's endpoints and keys are already set.
Nothing secret is stored in this repo.

**1. Open the VM terminal** and get the code ready (about 2 minutes, first time only):

```sh
cd ~ && [ -d VizAgent ] || git clone https://github.com/bhargavchintam/VizAgent.git
cd ~/VizAgent && git pull
[ -d .venv ] || bash scripts/vm_setup.sh
source .venv/bin/activate
```

**2. Run the VM side in one command:**

```sh
python scripts/vm_run.py
```

It does five things in order and prints what it finds:

| Step | What it does |
|------|--------------|
| `probe` | Prints the shape of real responses and tries one Cosmos second look |
| `before` | Saves `app/snap_before.json`: the sweep on the original descriptions. Never overwritten |
| `reingest` | Picks the chunks with the most conflict candidates, asks `[y/N]`, re-ingests them with our prompt and waits |
| `after` | Saves `app/snap_after.json`: the sweep on the new descriptions |
| `deploy` | Deploys the app and prints its address |

Only one person should run it, because the re-ingest rewrites descriptions in the team's shared index.
The whole run takes up to about 20 minutes; most of that is the re-ingest.

**3. If something fails,** the output says which step. Everything printed is saved to `vm_run_log.txt`
with bucket names, hosts and tokens masked, so it is safe to paste to a teammate. Steps can be re-run
on their own:

```sh
python scripts/vm_run.py after deploy     # e.g. after a code fix
```

**4. Open the app** at the `Live:` address the deploy step prints
(`http://video-lab-team-<N>.cosmos.vastdata.com/app/`) and press **Run sweep**.

**5. Review and act.** Mark clips 👍 or 👎, press **Draft work order** on the top hotspot, then
**Save review to W&B**.

To redeploy after a code change: `git pull && bash deploy/deploy.sh`.

## Two-minute demo

1. **The problem.** Severe crashes are up, and the city must now publish safety dashboards.
2. **Plain search.** It returns pedestrians but cannot tell a conflict from a normal crossing.
3. **The sweep.** Steps run live. The funnel shows what was verified and what was filtered out, with reasons.
4. **Before and after.** The same sweep on the original descriptions and on ours.
5. **The action.** Approve clips at the top hotspot and draft the work order.
6. **The proof.** The review score, and the trace and evaluation in W&B Weave.

We say "conflicts", not "near-misses", unless a clip clearly shows one, and we never claim distances or speeds.

## Switches

Set at deploy time, for example `VIZ_COSMOS=0 bash deploy/deploy.sh`. The page only shows a
feature's button when `/health` reports it on.

| Switch | Default | What it controls |
|--------|---------|------------------|
| `LLM_MODEL` | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B` | The grading model (falls back to `openai/gpt-oss-20b` on error) |
| `VIZ_COSMOS` | on | Live Cosmos second look. Also needs the GPU endpoint from the team config |
| `VIZ_PUBLISH` | on | Save the review to W&B Weave. Also needs W&B tracing |
| `VIZ_WATCH` | on | Check newly indexed clips and raise alerts |
| `VIZ_CONTEXT` | on | Show the clips just before and after a conflict |
| `VIZ_REINGEST` | off | Re-ingest from the app. Off by default because it rewrites the shared index |

## Develop without the VM

Fixture mode serves a sample sweep and makes no network calls, so the page works on any laptop:

```sh
pip install -r requirements-dev.txt
VIZ_MODE=fixture python app/main.py       # http://localhost:8080/
pytest                                    # the test suite needs no network either
```

Routes and JSON shapes are in [API.md](API.md).

## Repo layout

```
app/                 # deployed as-is: a flat folder shipped as a Kubernetes ConfigMap (< 1 MiB)
  main.py            # FastAPI routes
  sweep.py           # the sweep: search, checks, decide, grade, rank, jobs, snapshots, watch
  taxonomy.py        # conflict types, the re-ingest prompt, causes and fixes
  workorder.py       # the Open311-style draft ticket
  extras.py          # context clips, review as a Weave evaluation, re-ingest loop
  gpu.py             # Cosmos3-Reason client for the live second look
  vss.py             # client for the team's VSS backend
  llm.py             # W&B inference client + Weave tracing
  index.html         # the page
  snap_sample.json   # sample sweep for fixture mode
deploy/deploy.sh     # deploy to http://<team-host>/app
scripts/vm_run.py    # the whole VM side in one command
scripts/probe.py     # prints masked shapes of real responses
skills/              # the agent packaged as an Agent Skill
tests/               # pytest suite
API.md               # the contract between the engine and the page
```

## Team

- Bindu Bhargava Reddy Chintam
- Sripadha
