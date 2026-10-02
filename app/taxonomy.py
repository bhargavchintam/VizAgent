"""What ViZ Agent looks for: conflict types, the re-ingest prompt and the menu of fixes."""

# Verdict letters written by Cosmos Reason at re-ingest and by the live second look.
VERDICTS = {
    "A": "near-miss or contact",
    "B": "conflict",
    "C": "normal yielding",
    "D": "no interaction",
}

# custom_prompt for POST /dashboard/reingest (the API caps it at 800 characters).
REINGEST_PROMPT = (
    "You are a traffic-safety observer. If the clip is filmed from a moving car, treat the camera car as a vehicle. "
    "List each pedestrian, cyclist and vehicle with its motion (crossing, waiting, turning, stopping, parked) "
    "and position relative to crosswalks, lanes and curbs. "
    "Say whether the camera car is moving or stopped. "
    "Then classify the closest pedestrian-vehicle interaction: "
    "(A) near-miss or contact: hard braking, swerving or a pedestrian jumping back; "
    "(B) conflict: pedestrian or cyclist in the path of a moving vehicle, no evasive action; "
    "(C) normal: vehicle waits or pedestrian has clear space; "
    "(D) none. "
    "Give a one-sentence physical reason. End with the line VERDICT: A, B, C or D."
)
assert len(REINGEST_PROMPT) <= 800

FHWA = "FHWA Proven Safety Countermeasure"
PRACTICE = "Common practice"

# Each type has search phrasings for fixed street cameras and for the dashcam (where the
# vehicle that matters is the one holding the camera), a direct question for the Cosmos
# second look, and candidate fixes, best first.
CONFLICT_TYPES = [
    {
        "key": "failure_to_yield",
        "label": "Failure to yield at a crosswalk",
        "queries": {
            "fixed": ["vehicle drives through the crosswalk while a pedestrian is crossing"],
            "dashcam": [
                "pedestrian crossing in the crosswalk ahead while the camera car is moving",
                "car ahead does not stop for a pedestrian crossing at the crosswalk",
            ],
        },
        "question": "Did a vehicle enter or pass through the crosswalk while a pedestrian was in it?",
        "countermeasures": [
            {
                "name": "Leading pedestrian interval",
                "source": FHWA,
                "why": "gives pedestrians a head start before vehicles get a green",
            },
            {
                "name": "Crosswalk visibility enhancements",
                "source": FHWA,
                "why": "high-visibility markings, advance stop lines and lighting make people crossing easier to see",
            },
        ],
    },
    {
        "key": "turning_conflict",
        "label": "Vehicle turning across a pedestrian's path",
        "queries": {
            "fixed": ["car turning at the intersection while a pedestrian crosses in front of it"],
            "dashcam": [
                "camera car turning while a pedestrian crosses in front of it",
                "vehicle turning across the crosswalk close to a pedestrian",
            ],
        },
        "question": "Did a turning vehicle cross the path of a pedestrian who was in the roadway?",
        "countermeasures": [
            {
                "name": "Leading pedestrian interval",
                "source": FHWA,
                "why": "puts pedestrians in the crosswalk before turning vehicles start to move",
            },
            {
                "name": "Turn calming (hardened centerline or slow-turn wedge)",
                "source": PRACTICE,
                "why": "forces slower, squarer turns so drivers see people in the crosswalk",
            },
        ],
    },
    {
        "key": "midblock_crossing",
        "label": "Mid-block crossing in front of a moving vehicle",
        "queries": {
            "fixed": ["pedestrian crossing the street outside a crosswalk in front of a moving car"],
            "dashcam": [
                "pedestrian steps into the road mid-block in front of the camera car",
                "pedestrian crossing the street between parked cars ahead of the camera car",
            ],
        },
        "question": "Did a pedestrian cross outside a crosswalk in the path of a moving vehicle?",
        "countermeasures": [
            {
                "name": "Rectangular rapid flashing beacon at a marked mid-block crosswalk",
                "source": FHWA,
                "why": "gives people a marked, signalled place to cross where they already do",
            },
            {
                "name": "Median and pedestrian refuge island",
                "source": FHWA,
                "why": "lets people cross one direction of traffic at a time",
            },
        ],
    },
    {
        "key": "blocked_crosswalk",
        "label": "Vehicle stopped on the crosswalk",
        "queries": {
            "fixed": ["vehicle stopped on the crosswalk while pedestrians walk around it"],
            "dashcam": ["vehicle stopped on the crosswalk ahead with pedestrians walking around it"],
        },
        "question": "Was a vehicle stopped on the crosswalk while a pedestrian had to walk around it?",
        "countermeasures": [
            {
                "name": "Crosswalk visibility enhancements",
                "source": FHWA,
                "why": "an advance stop line and restricted parking at the approach keep the crosswalk clear",
            },
            {
                "name": "Don't-block-the-box markings and enforcement",
                "source": PRACTICE,
                "why": "discourages drivers from entering a crossing they cannot clear",
            },
        ],
    },
]

TYPE_BY_KEY = {t["key"]: t for t in CONFLICT_TYPES}

GRADE_PROMPT = """You grade pedestrian-vehicle conflicts for a city traffic-safety engineer.
You get one video clip's written description, the conflict type the search was looking for,
the verdict letter saved with the description (may be missing) and an object-detector summary.
Judge only from what the description states. Do not invent distances, speeds or events.
Severity scale:
0 = no conflict
1 = low: a pedestrian and a vehicle interact with clear space
2 = medium: a pedestrian or cyclist is in a moving vehicle's path, no evasive action
3 = high: hard braking, swerving, a pedestrian stepping back, or contact
Reply with JSON only: {"conflict": true|false, "severity": 0-3, "reason": "<one sentence, at most 20 words>"}"""

SECOND_LOOK_PROMPT = (
    "Watch the clip. If it is filmed from a moving car, treat the camera car as a vehicle. {question} "
    "Then classify the closest pedestrian-vehicle interaction: "
    "(A) near-miss or contact: hard braking, swerving or a pedestrian jumping back; "
    "(B) conflict: pedestrian or cyclist in the path of a moving vehicle, no evasive action; "
    "(C) normal: vehicle waits or pedestrian has clear space; "
    "(D) none. "
    "Answer in this format: <think>your reasoning in two or three sentences</think><answer>one letter</answer>"
)
