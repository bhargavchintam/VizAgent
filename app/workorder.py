"""Turn verified conflicts into a drafted work order.

The ticket follows the Open311 GeoReport v2 service-request fields so a city system could
ingest it. It is drafted only: nothing here sends it anywhere. Locations come from the
index (camera, city, drive and time offset); GPS coordinates are never invented.
"""

import hashlib
from collections import Counter
from datetime import datetime
from urllib.parse import quote

import taxonomy


def _clock(seconds):
    if not isinstance(seconds, (int, float)):
        return None
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _where(conflict):
    place = (conflict.get("location") or "location not in the index").replace("_", " ")
    if conflict.get("view") == "fixed":
        return f"Camera {conflict.get('camera_id')}, {place}"
    drive = str(conflict.get("original_video") or conflict.get("camera_id")).rsplit("/", 1)[-1]
    at = _clock(conflict.get("start_sec"))
    return f"Drive {drive}{f' @ {at}' if at else ''}, {place}"


def _priority(conflicts):
    worst = max(c.get("severity", 0) for c in conflicts)
    if worst >= 3:
        return "P1"
    return "P2" if worst == 2 or len(conflicts) >= 3 else "P3"


def draft(conflicts):
    """Build the WorkOrder for a set of approved conflicts, worst first."""
    conflicts = sorted(conflicts, key=lambda c: c.get("severity", 0), reverse=True)
    top = conflicts[0]
    counts = Counter(c["type"]["key"] for c in conflicts)
    type_key = max(counts, key=lambda k: (counts[k], max(c["severity"] for c in conflicts if c["type"]["key"] == k)))
    ctype = taxonomy.TYPE_BY_KEY.get(type_key) or {"label": type_key, "countermeasures": []}
    fixes = ctype["countermeasures"]
    fix = fixes[0] if fixes else {"name": "Site review by a traffic engineer", "source": "", "why": ""}
    priority = _priority(conflicts)
    address = _where(top) if top.get("view") == "fixed" else f"{len({c.get('original_video') for c in conflicts})} dashcam drive(s), see evidence"

    evidence = [
        {
            "conflict_id": c["id"],
            "source": c["source"],
            "where": _where(c),
            "type": c["type"]["label"],
            "severity": c.get("severity", 0),
            "reason": c.get("reason") or "",
        }
        for c in conflicts
    ]
    digest = hashlib.sha1("".join(sorted(c["id"] for c in conflicts)).encode()).hexdigest()[:4].upper()
    order_id = f"VZ-{datetime.now().astimezone():%Y%m%d}-{digest}"
    description = (
        f"{len(conflicts)} verified pedestrian-vehicle conflict(s), most often: {ctype['label'].lower()}. "
        f"Recommended fix: {fix['name']} ({fix['why']}). "
        "Each clip was checked against the object detector and a Cosmos Reason verdict before it was listed."
    )

    lines = [
        f"# Work order {order_id} (DRAFT)",
        "",
        f"- **Priority:** {priority}",
        f"- **Location:** {address}",
        f"- **Pattern:** {len(conflicts)} verified conflict(s), most often {ctype['label'].lower()}",
        f"- **Recommended fix:** {fix['name']}" + (f" ({fix['source']})" if fix["source"] else ""),
        f"- **Why this fix:** {fix['why']}" if fix["why"] else "",
    ]
    if len(fixes) > 1:
        lines.append("- **Alternatives:** " + "; ".join(f["name"] for f in fixes[1:]))
    lines += ["", "## Evidence", ""]
    lines += [f"{i}. {e['where']}: {e['type']}, severity {e['severity']}/3. {e['reason']}" for i, e in enumerate(evidence, 1)]
    lines += ["", "_Drafted by ViZ Agent from video evidence. Not sent. Needs engineer approval._"]

    return {
        "id": order_id,
        "status": "DRAFT",
        "open311": {
            "service_code": f"VZ-{type_key}",  # our own mapping, not a city's published code list
            "service_name": f"Street safety: {ctype['label']}",
            "description": description,
            "address_string": address,
            "media_url": f"api/stream?source={quote(top['source'], safe='')}",
            "attribute": {
                "priority": priority,
                "conflict_type": type_key,
                "countermeasure": fix["name"],
                "countermeasure_source": fix["source"],
                "alternatives": [f["name"] for f in fixes[1:]],
                "evidence": evidence,
            },
        },
        "markdown": "\n".join(line for line in lines if line is not None),
    }
