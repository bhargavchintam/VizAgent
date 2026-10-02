"""Summarize the before/after sweep snapshots: what was kept, what was dropped, and why.

On the VM (reads the live app, using INGRESS_URL from the environment):
    python3 scripts/snapshot_summary.py
Or from files:
    python3 scripts/snapshot_summary.py app/snap_before.json app/snap_after.json
Output is masked (no bucket names), so it is safe to paste.
"""

import collections
import json
import os
import re
import sys
import urllib.request


def mask(text):
    return re.sub(r"s3://[^/\s\"']+", "s3://<bucket>", str(text))


def load(arg):
    if os.path.exists(arg):
        with open(arg, encoding="utf-8") as handle:
            return arg, json.load(handle)
    base = os.environ.get("INGRESS_URL", "").rstrip("/")
    url = f"{base}/app/api/snapshot/{arg}" if base else arg
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return url, json.load(response)
    except Exception as exc:
        return url, {"error": f"{type(exc).__name__}: {exc}"}


def summarize(name, snap):
    print(f"\n===== {mask(name)} =====")
    if "error" in snap:
        print("not available:", mask(snap["error"]))
        return
    conflicts = snap.get("conflicts", [])
    count = collections.Counter
    print("run:", snap.get("generated_at"), "cameras:", snap.get("cameras"))
    print("funnel:", snap.get("funnel"))
    print("by status:", dict(count(c["status"] for c in conflicts)))
    print("reject reasons:", count(c["reject_reason"] for c in conflicts if c["reject_reason"]).most_common(8))
    print("saved verdicts:", dict(count(str(c["signals"]["stored"].get("letter")) for c in conflicts)))
    print("causes:", dict(count(str(c["signals"]["stored"].get("cause")) for c in conflicts)))
    print("detector ok:", dict(count(str(c["signals"]["yolo"].get("ok")) for c in conflicts)))
    print("detector notes:", count(c["signals"]["yolo"].get("note") for c in conflicts).most_common(5))
    print("by camera:", dict(count(c["camera_id"] for c in conflicts)))
    print("by type:", dict(count(c["type"]["key"] for c in conflicts)))
    print("severity:", dict(count(c["severity"] for c in conflicts)))
    for conflict in conflicts:
        if conflict["status"] == "verified":
            print(f"  VERIFIED {conflict['camera_id']} {conflict['type']['key']} sev {conflict['severity']}: {conflict['reason'][:100]}")
    for conflict in conflicts[:3]:
        print(f"  caption sample [{conflict['status']}]: {mask(conflict['caption'][:220])!r}")


def main():
    names = sys.argv[1:] or ["before", "after"]
    for name in names:
        summarize(*load(name))


if __name__ == "__main__":
    main()
