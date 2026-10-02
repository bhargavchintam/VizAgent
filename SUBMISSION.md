# team-20

## Project
ViZ Agent turns street and dashcam video into verified pedestrian–vehicle conflict reports for city traffic engineers. It sweeps for four conflict types, double-checks each clip with Cosmos verdicts and object detection, explains why, and drafts the work order.

**Stack:** VAST AI OS (S3, DataEngine, VastDB) through the VSS retrieval API; NVIDIA Cosmos3-Reason for clip descriptions and a live second look; Cosmos Embed1 hybrid search; YOLO11 detections; Nemotron on W&B serverless inference for grading; W&B Weave for traces, the reviewed dataset and the human-review evaluation; FastAPI and plain HTML/JS, deployed on Kubernetes as a ConfigMap.
**Code:** https://github.com/bhargavchintam/VizAgent
**Live app:** https://team-20-app.thecosmoslabs.com/app/
**Supplementary:** Demo video (2 min, made with HeyGen HyperFrames): https://drive.google.com/file/d/1K9Tf1ObIpW3gEpsApN-hMGbWA5NjscKa/view?usp=sharing

## Feedback
The VSS search API plus the Cosmos captions made it quick to build a real agent, and the Cursor skills helped. Pain points: the 4 GiB backend was OOM-killed with 4 parallel searches, so a documented safe concurrency would help. config.example lists `nvidia/cosmos3-reason`, but the endpoint serves `nvidia/cosmos3-nano-reasoner`. Pods could not resolve the team hostname until we added hostAliases. Re-ingest jobs stayed "pending" with no status detail.
