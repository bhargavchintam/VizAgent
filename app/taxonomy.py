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
    "Give a one-sentence physical reason. "
    "Then a line CAUSE: blocked_view, turning_vehicle, no_crosswalk, did_not_slow or none. "
    "End with the line VERDICT: A, B, C or D."
)
assert len(REINGEST_PROMPT) <= 800

# The physical reason Cosmos writes on the CAUSE line. The cause, more than the conflict
# type, decides which fix a city engineer should try first.
CAUSES = {
    "blocked_view": "a parked or stopped vehicle hid the pedestrian or the driver's view",
    "turning_vehicle": "a turning vehicle crossed the pedestrian's path",
    "no_crosswalk": "the pedestrian crossed where there is no crosswalk",
    "did_not_slow": "the vehicle did not slow down for the pedestrian",
}

# Crash-reduction figures quoted from FHWA's Proven Safety Countermeasures pages.
EFFECTS = {
    "Leading pedestrian interval": "can reduce related crashes by about 13% (FHWA)",
    "Rectangular rapid flashing beacon at a marked mid-block crosswalk": "can reduce pedestrian crashes by up to 47% (FHWA)",
    "Median and pedestrian refuge island": "can reduce pedestrian crashes by up to 56% in urban and suburban areas (FHWA)",
    "Speed safety camera": "can reduce fatal and injury crashes by 20-37% (FHWA)",
}


FHWA = "FHWA Proven Safety Countermeasure"
PRACTICE = "Common practice"

# Dashcam phrasings say "ego vehicle": that is how Cosmos Reason's own dashcam captions name
# the car holding the camera (7 of 58 captions; none say "camera car").
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
                "pedestrian crossing in the crosswalk ahead while the ego vehicle is moving",
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
                "ego vehicle turning while a pedestrian crosses in front of it",
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
                "pedestrian steps into the road mid-block in front of the ego vehicle",
                "pedestrian crossing the street between parked cars ahead of the ego vehicle",
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

DAYLIGHTING = {
    "name": "Daylighting: no parking within 20 ft of the crosswalk",
    "source": "California AB 413; SF Street Safety Act calls for hardened daylighting",
    "why": "removes the parked vehicles that hide people about to cross",
}
SPEED_CAMERA = {
    "name": "Speed safety camera",
    "source": FHWA + "; SF runs speed cameras at 33 sites under AB 645",
    "why": "slows drivers on the approach so they can stop for people crossing",
}


def _fix(type_key, name):
    return next(f for f in TYPE_BY_KEY[type_key]["countermeasures"] if f["name"] == name)


CAUSE_FIXES = {
    "blocked_view": [DAYLIGHTING, _fix("failure_to_yield", "Crosswalk visibility enhancements")],
    "turning_vehicle": [_fix("turning_conflict", "Leading pedestrian interval"), _fix("turning_conflict", "Turn calming (hardened centerline or slow-turn wedge)")],
    "no_crosswalk": TYPE_BY_KEY["midblock_crossing"]["countermeasures"],
    "did_not_slow": [SPEED_CAMERA, _fix("failure_to_yield", "Leading pedestrian interval")],
}


def fixes_for(type_key, cause=None):
    """Candidate fixes, best first: by physical cause when Cosmos recorded one, else by conflict type."""
    if cause in CAUSE_FIXES:
        return CAUSE_FIXES[cause]
    return TYPE_BY_KEY.get(type_key, {}).get("countermeasures", [])

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
