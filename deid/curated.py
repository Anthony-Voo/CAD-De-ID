"""Hand-curated identities from the 64 catalog titles (reviewed one by one).

(text, class, content): content=False means rename in paths only, because the
token is short or a common word and would collide with ordinary file text.
"""
CATALOG = [
    # organizations / institutions
    ("AWI", "org", False), ("Aarhus", "org", True), ("AdReNa", "org", True), ("BAM", "org", False),
    ("Buildlog", "org", True), ("CNR-STIIMA", "org", True), ("FabCity Hamburg", "org", True),
    ("Curious Community Labs", "org", True), ("LBNL", "org", True), ("LulzBot", "org", True),
    ("Aleph Objects", "org", True), ("NIST", "org", True), ("NTNU", "org", True), ("OSE", "org", False),
    ("Open Source Ecology", "org", True), ("Open Lab Automata", "org", True), ("EPFL", "org", True),
    ("IIT Kanpur", "org", True), ("RWTH Aachen", "org", True), ("TU Delft", "org", True),
    ("UKCEH", "org", True), ("UNH", "org", False), ("University Ljubljana", "org", True),
    ("University Michigan", "org", True), ("University Southampton", "org", True),
    ("University Stuttgart", "org", True), ("ZSW", "org", False), ("Muzi Works", "org", True),
    ("Vhéliotech", "org", True), ("vhélio", "org", True), ("OST", "org", False), ("ohwr", "org", True),
    # people
    ("Dominique Lachiver", "person", True), ("Maximilian Behm", "person", True),
    ("Maxwell Lohss", "person", True),
    # named projects / products
    ("FENRIR", "project", True), ("MOUSE", "project", False), ("Pipettin", "project", True),
    ("LOCILAMP", "project", True), ("Trillium", "project", False), ("OpenSourceLeg", "project", True),
    ("HyFaB", "project", True), ("PowerCube6", "project", True), ("CNCCMV2", "project", True),
    ("ColdSawV1", "project", True), ("ThrowBot", "project", True), ("ORD Bot", "project", True),
    ("Tiny Oat Collider", "project", True), ("BASE DUO", "project", True), ("ARC-RWS", "project", True),
    ("diot-crate-hw", "project", True), ("wr-switch-hw-v4", "project", True),
]
