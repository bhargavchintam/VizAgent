"""VizAgent use-case logic.

The pattern: VSS search finds candidate moments -> YOLO detections give a second,
independent signal -> the LLM judges each moment and decides what to do.
Swap the prompt and scoring below for the chosen use case.
"""

import llm
from vss import VSSClient

JUDGE_PROMPT = """You review video segments for a safety team.
For the event described by the user, decide from the segment description and the
object detections whether the event really happens in this segment.
Reply with JSON only: {"match": true|false, "severity": 1-5, "reason": "<one sentence>"}"""


def _detection_summary(vss, source):
    try:
        det = vss.detections(source)
    except Exception:
        return "no detections available"
    return str(det)[:1500]  # keep the prompt small; refine once the sidecar shape is known


@llm.op
def judge(event, segment, detections):
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {
            "role": "user",
            "content": f"Event: {event}\n\nSegment description:\n{segment.get('reasoning_content', '')}"
            f"\n\nDetections:\n{detections}",
        },
    ]
    try:
        return llm.chat_json(messages)
    except Exception as exc:
        return {"match": None, "severity": 0, "reason": f"judge failed: {exc}"}


@llm.op
def triage(vss: VSSClient, event, top_k=10, **filters):
    """Search for an event, verify each hit, and return hits ranked by severity."""
    hits = vss.search(event, top_k=top_k, **filters).get("results", [])
    ranked = []
    for hit in hits:
        source = hit.get("source")
        verdict = judge(event, hit, _detection_summary(vss, source)) if llm.configured() else {}
        ranked.append({**hit, "verdict": verdict})
    ranked.sort(key=lambda h: (h["verdict"].get("severity") or 0, h.get("similarity_score") or 0), reverse=True)
    return {"event": event, "results": ranked}
