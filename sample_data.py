"""Fictional sample data for the NeighborTools classroom demo.

Every owner, participant, and item below is made up for a class assignment.
Availability is stored as day offsets from "today", so the demo never expires.
"""

RESEARCH_QUESTION = (
    "When five rural homeowners who maintain their own property and need equipment "
    "for a job within the next seven days first open a board of available equipment "
    "from nearby neighbors or acquaintances, do at least three submit a genuine "
    "borrowing request within 48 hours?"
)

OWNERS = [
    {"id": "dale", "firstName": "Dale", "area": "Miller Creek Rd, near the old grange hall"},
    {"id": "ruth", "firstName": "Ruth", "area": "County Rd 12, red barn by the mailbox cluster"},
    {"id": "marcus", "firstName": "Marcus", "area": "Hollis Lane, end of the gravel drive"},
]

# Five fictional participants. IDs are fixed so their links keep working after a reset.
PARTICIPANTS = [
    {"id": "k7m2q", "label": "Participant 1", "name": "Claire"},
    {"id": "r4t8w", "label": "Participant 2", "name": "Owen"},
    {"id": "h3n6z", "label": "Participant 3", "name": "Teresa"},
    {"id": "b9x5c", "label": "Participant 4", "name": "Hank"},
    {"id": "f2v7j", "label": "Participant 5", "name": "Mei"},
]

# fromOffset/toOffset: first and last available day, counted in days from today.
SAMPLE_TOOLS = [
    {"id": "t1", "ownerId": "dale", "name": "Gas pressure washer",
     "description": "3,100 PSI with a 25-ft hose and three nozzle tips. Good for siding, decks, "
                    "and farm equipment. Bring your own gas.",
     "pickupArea": "Miller Creek Rd, near the old grange hall", "fromOffset": 0, "toOffset": 21},
    {"id": "t2", "ownerId": "dale", "name": "24-ft extension ladder",
     "description": "Aluminum, 300 lb rating. Fits in a pickup bed with the tailgate down.",
     "pickupArea": "Miller Creek Rd, near the old grange hall", "fromOffset": 1, "toOffset": 14},
    {"id": "t3", "ownerId": "dale", "name": "Wheelbarrow",
     "description": "6 cu ft steel tub with a pneumatic tire. Recently patched and holds air fine.",
     "pickupArea": "Miller Creek Rd, near the old grange hall", "fromOffset": 0, "toOffset": 30},
    {"id": "t4", "ownerId": "ruth", "name": "Cordless hedge trimmer",
     "description": "24-inch blade with two 40V batteries and a charger.",
     "pickupArea": "County Rd 12, red barn by the mailbox cluster", "fromOffset": 0, "toOffset": 10},
    {"id": "t5", "ownerId": "ruth", "name": "Post-hole digger",
     "description": "Manual clamshell digger with fiberglass handles, for fence and mailbox posts.",
     "pickupArea": "County Rd 12, red barn by the mailbox cluster", "fromOffset": 2, "toOffset": 25},
    {"id": "t6", "ownerId": "ruth", "name": "Backpack leaf blower",
     "description": "Gas blower that uses 50:1 mixed fuel (a small can is included).",
     "pickupArea": "County Rd 12, red barn by the mailbox cluster", "fromOffset": 3, "toOffset": 20},
    {"id": "t7", "ownerId": "marcus", "name": "Rear-tine tiller",
     "description": "About 200 lb. Needs a truck with a ramp; Marcus can help load it.",
     "pickupArea": "Hollis Lane, end of the gravel drive", "fromOffset": 5, "toOffset": 28},
    {"id": "t8", "ownerId": "marcus", "name": "Electric pole saw",
     "description": "10-inch bar that reaches about 9 ft. Needs an outdoor extension cord (not included).",
     "pickupArea": "Hollis Lane, end of the gravel drive", "fromOffset": 0, "toOffset": 12},
    {"id": "t9", "ownerId": "marcus", "name": "6-ft fiberglass step ladder",
     "description": "Sturdy and non-conductive. Good for gutters on single-story buildings.",
     "pickupArea": "Hollis Lane, end of the gravel drive", "fromOffset": 1, "toOffset": 21},
]
