# team-20

## Project
ViZ Agent turns street and dashcam video into verified pedestrian–vehicle conflict reports for city traffic engineers. It sweeps for four conflict types, double-checks each clip with Cosmos verdicts and object detection, explains why, and drafts the work order.

**Stack:** VAST AI OS (S3, DataEngine, VastDB) through the VSS retrieval API; NVIDIA Cosmos3-Reason for clip descriptions and a live second look; Cosmos Embed1 hybrid search; YOLO11 detections; Nemotron on W&B serverless inference for grading; W&B Weave for traces, the reviewed dataset and the human-review evaluation; FastAPI and plain HTML/JS, deployed on Kubernetes as a ConfigMap.
**Code:** https://github.com/bhargavchintam/VizAgent
**Live app:** http://video-lab-team-20.cosmos.vastdata.com/app/ (reachable inside the workshop VM only)
**Supplementary:** NOT PROVIDED

## Feedback
NOT PROVIDED
