# VizAgent

A video agent built at the **VAST Builders Challenge: Real-Time Video Agents Hack** (San Francisco, Oct 2, 2026).

VizAgent searches an indexed video archive in plain language, checks each hit with a second signal
(YOLO detections), and uses an LLM to judge and rank what it found, so a person sees the moments that
matter first instead of a long list of clips.

> Use case and demo story: _to be finalised on build day._

## Stack

| Layer | What we use |
|-------|-------------|
| Video ingest + index | VAST AI OS: S3, DataEngine, VastDB (VSS Blueprint pipeline) |
| Video understanding | NVIDIA Cosmos3-Reason (segment descriptions), Cosmos Embed1 (hybrid search vectors) |
| Object detection | YOLO11 (Ultralytics) bounding boxes per segment |
| Agent reasoning | Weights & Biases (CoreWeave) serverless inference, OpenAI-compatible API |
| Observability | W&B Weave tracing of every agent step |
| GPUs | CoreWeave |
| App | Python 3.12, FastAPI, plain HTML/JS |
| Built with | Cursor + the challenge's agent skills |

## How it works

```
browser ──> VizAgent (FastAPI, /app on the team host)
              ├── /api/search   ─> VSS POST /api/v1/search           (ranked moments)
              ├── /api/ask      ─> VSS POST /api/v1/agent/ask        (grounded answer)
              ├── /api/triage   ─> search ─> YOLO detections ─> LLM judge ─> ranked by severity
              └── /api/stream   ─> VSS /api/v1/videos/stream         (proxied, token stays server-side)
```

## Repo layout

```
app/                 # deployed as-is (flat folder, shipped as a Kubernetes ConfigMap, < 1 MiB)
  main.py            # FastAPI app and routes
  vss.py             # client for the team's VSS retrieval backend
  llm.py             # W&B inference client + Weave tracing
  agent.py           # use-case logic: search -> verify -> judge -> rank
  index.html         # UI
  requirements.txt   # runtime dependencies (installed at pod start)
deploy/deploy.sh     # deploy to http://<team-host>/app
scripts/vm_setup.sh  # one-time setup on the workshop VM
tests/               # pytest smoke tests
```

## Run it (on the workshop VM)

Everything runs on the VM, where the team's endpoints and keys are already exported as environment
variables. Nothing secret is stored in this repo.

```sh
git clone https://github.com/bhargavchintam/VizAgent.git ~/VizAgent
cd ~/VizAgent
bash scripts/vm_setup.sh          # copies Cursor skills, creates .venv with all dependencies
source .venv/bin/activate
python app/main.py                # dev server on http://localhost:8080/
pytest                            # tests
```

Deploy the live app (re-run after every change):

```sh
bash deploy/deploy.sh             # prints http://video-lab-team-<N>.cosmos.vastdata.com/app/
```

## Configuration

See [.env.example](.env.example). Variable names only; values come from `/config/<team>.config` on the VM.
`LLM_MODEL` picks the W&B inference model (default `meta-llama/Llama-3.1-8B-Instruct`).

## Team

- Bindu Bhargava Reddy Chintam
