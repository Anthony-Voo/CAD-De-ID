"""Seed identities derived from the corpus layout (manifest paths).

Layout:
  archives/<owner>__<repo>[__<sha>].zip
  sources/github/<owner>__<repo>[__<sha>]/...
  sources/gitlab/gitlab.com__<group>[__<sub>]__<project>/<ref>/<project>.extracted/...
  sources/web/<domain>/...
  sources/catalog/<project title or domain>/<hash>/...
  sources/existing/<collection>/<subset>/...
"""
from __future__ import annotations

import re

from .registry import Registry

# Software, formats and standards: never pseudonymize these words inside file
# content (they are format-required or name the tool, not the owner).
SOFTWARE = {
    "freecad", "solidworks", "autodesk", "inventor", "fusion360", "onshape", "catia", "creo", "siemens",
    "opencascade", "step", "iges", "parasolid", "acis", "spatial", "kicad", "openscad", "blender", "python",
    "github", "gitlab", "cad", "lego", "technic", "nema17", "astm", "iso", "din", "ansi", "steam", "cnc",
    "ptc", "solidedge", "rhino", "sketchup", "dassault", "microsoft", "adobe", "google",
}

# Generic words that appear as repo names or path parts and must not be
# replaced inside content (path position is handled separately).
GENERIC = {
    "mechanical", "hardware", "cad", "cad-files", "cad-projects", "parts", "resources", "me", "lc", "evo",
    "fab", "pro", "forge", "hinge", "cnc", "academy", "microscope", "solidworks", "3dmodels", "cadmodels",
    "mechanical-parts", "3d_models", "mechanicaldesign", "design", "models", "drawings", "project",
    "ventilator", "huxley", "ormerod", "crayford", "visebase", "atelier", "remorque", "poseidon", "omi",
    "seedmint", "openframe", "s1", "cmpe2150", "me134", "cadam", "jubilee", "materializr", "mayo",
    "partcad", "build123d", "vcad", "forge", "openamr", "openwave", "megaruptor", "fluxpad", "cosi-measure",
    "fabricatable-machines", "engineering-design-models", "cad-libraries", "cad-mechanics-public",
}

TLD2 = {"co", "ac", "com", "edu", "gov", "org", "net", "or", "ne", "go", "lg"}
DOMAIN_KEEP = {"www", "com", "org", "net", "edu", "gov", "io", "dev", "info", "uk", "de", "fr", "cn", "jp", "es", "nl",
               "eu", "at", "ar", "mx", "in", "tr", "hu", "lu", "pt", "ec", "iq", "ac", "co", "or", "lg", "us",
               "files", "cdn", "static", "assets", "media", "api", "download", "downloads", "public", "prod", "ftp",
               "wiki", "home", "data", "blob", "core", "windows", "github", "wordpress", "s3", "amazonaws",
               "cloudfront", "storage", "googleapis", "tokyo", "metro", "gob"}

# Hand-curated identifiers from catalog titles and the "existing" collections:
# organizations, people, projects and project codes.  (kind, text, ctx)
ALL = ("path", "text", "bin")
TXT = ("path", "text")
CURATED = [
    # organizations / institutions
    ("org", "AWI", TXT), ("org", "Aarhus", ALL), ("org", "AdReNa", ALL), ("org", "BAM", ("path",)),
    ("org", "Buildlog", ALL), ("org", "CNR-STIIMA", ALL), ("org", "STIIMA", ALL), ("org", "FabCity Hamburg", ALL),
    ("org", "Curious Community Labs", ALL), ("org", "LBNL", ALL), ("org", "LulzBot", ALL),
    ("org", "Aleph Objects", ALL), ("org", "Muzi Works", ALL), ("org", "NIST", TXT), ("org", "usnistgov", ALL),
    ("org", "NTNU", ALL), ("org", "OSE", ("path",)), ("org", "Open Source Ecology", ALL),
    ("org", "opensourceecology", ALL), ("org", "OST", ("path",)), ("org", "Open Lab Automata", ALL),
    ("org", "EPFL", TXT), ("org", "IIT Kanpur", ALL), ("org", "IIT", ("path",)), ("org", "iitk", TXT),
    ("org", "iitm", TXT), ("org", "RWTH Aachen", ALL), ("org", "RWTH", TXT), ("org", "TU Delft", ALL),
    ("org", "UKCEH", ALL), ("org", "UNH", TXT), ("org", "University Ljubljana", ALL),
    ("org", "University Michigan", ALL), ("org", "University Southampton", ALL),
    ("org", "University Stuttgart", ALL), ("org", "ZSW", TXT), ("org", "Vhéliotech", ALL),
    ("org", "Festo", ALL), ("org", "Misumi", ALL), ("org", "Marco Inc", ALL), ("org", "Omnimech", ALL),
    ("org", "Cambridge University Press", ALL), ("org", "LIGO", TXT), ("org", "Caltech", ALL),
    ("org", "Openwater", ALL), ("org", "OpenwaterHealth", ALL), ("org", "BCN3D", ALL),
    ("org", "Trillium", ALL), ("org", "robotcub", ALL), ("org", "Istituto Italiano di Tecnologia", ALL),
    ("org", "Thorlabs", ALL), ("org", "Ultimaker", ALL), ("org", "Prusa", ALL), ("org", "Sherline", ALL),
    ("org", "Drexel", ALL), ("org", "Spatial Corp", TXT), ("org", "ohwr", TXT), ("org", "CERN", TXT),
    ("org", "G1MRA", ALL), ("org", "Sandia", ALL), ("org", "Haas", TXT), ("org", "ShopBot", ALL),
    ("org", "Dephy", ALL), ("org", "sdpsi", TXT),
    # people
    ("person", "Dominique Lachiver", ALL), ("person", "Maximilian Behm", ALL), ("person", "Maxwell Lohss", ALL),
    ("person", "Tom Kramer", ALL), ("person", "Keith Bucklitch", ALL), ("person", "Ulrich Rapp", ALL),
    ("person", "U.Rapp", ALL), ("person", "Fabian Voigt", ALL), ("person", "Andrew Marles", ALL),
    ("person", "Gerhart Zimmermann", ALL), ("person", "Manfred Osti", ALL), ("person", "Pete Brown", ALL),
    ("person", "Randy Wilson", ALL), ("person", "Sanjeev Trika", ALL), ("person", "Bill Regli", ALL),
    ("person", "William Regli", ALL), ("person", "Regli", ALL), ("person", "Cicirello", ALL),
    ("person", "Daniel Lapadat", ALL), ("person", "Lapadat", ALL), ("org", "Heriot Watt", ALL),
    ("org", "Heriot-Watt University", ALL), ("org", "Drexel University", ALL), ("org", "UMD", TXT), ("org", "OSU", TXT), ("org", "Qioptiq", ALL),
    ("project", "CEB", TXT), ("org", "CNPM", TXT), ("project", "OSU_USLI", ALL),
    ("org", "California Institute of Technology", ALL), ("org", "Massachusetts Institute of Technology", ALL),
    ("org", "CA Institute of Technology", ALL), ("org", "CA Inst of Technology", ALL),
    # projects / product lines / codenames
    ("project", "Pipettin", ALL), ("project", "pipettin-bot", ALL), ("project", "OpenSourceLeg", ALL),
    ("project", "Open Source Leg", ALL), ("project", "LOCILAMP", ALL), ("project", "ThrowBot", ALL),
    ("project", "PowerCube6", ALL), ("project", "PowerCube", ALL), ("project", "CEB Press", ALL),
    ("project", "Tiny Oat Collider", ALL), ("project", "HyFaB", ALL), ("project", "FENRIR", ALL),
    ("project", "ORD Bot", ALL), ("project", "MOUSE", ("path",)), ("project", "HDX-MS", TXT),
    ("project", "CTC_FTC", ALL), ("project", "dDNP_LOD-ESR", ALL), ("project", "ARC-RWS", ALL),
    ("project", "TRC Challenge", ALL), ("project", "MAS 2.0", ("path",)), ("project", "OSE_CNCCMV2", ALL),
    ("project", "OSE_ColdSawV1", ALL), ("project", "iCub", TXT), ("project", "icub2", TXT), ("project", "icub3", TXT),
    ("project", "ergoCub", ALL), ("project", "OpenLoong", ALL), ("project", "loong", ("path", "text")),
    ("project", "2p-RAM", ALL), ("project", "openlifu", ALL), ("project", "openmotion", ALL),
    ("project", "rolypolytoy", ALL), ("project", "mesoSPIM", ALL), ("project", "mesoscope", ("path",)),
    ("project", "odri", ("path",)), ("project", "penny-press", ("path",)), ("project", "HLTS", TXT),
    ("project", "omcs", ("path", "text")), ("project", "topmass", ALL), ("project", "Sigma", ("path",)),
    ("project", "hardgate", ALL), ("project", "wr-switch-hw-v4", ALL), ("project", "diot-crate-hw", ALL),
    ("project", "learning-tower", ALL), ("project", "PAROL6", ALL), ("project", "OpenSaint", ALL),
    ("project", "Coldsaw", ALL), ("project", "Cold Saw", ALL), ("project", "remorque PM", ALL),
    ("project", "Remorque PM v0", ALL), ("project", "Kinetix", ("path",)),
    # project codes used in part numbers
    ("code", "rc_iit", ALL), ("code", "rc_tlr", ALL), ("code", "rc_usal", ALL), ("code", "TLR", ("path", "text")),
    ("code", "usal", ("path", "text")), ("code", "TA00", ("path", "text")),
]


def split_owner_repo(seg: str):
    parts = seg.split("__")
    owner = parts[0]
    repo = parts[1] if len(parts) > 1 else ""
    sha = parts[2] if len(parts) > 2 and re.fullmatch(r"[0-9a-f]{6,40}", parts[2]) else None
    return owner, repo, sha


def domain_labels(dom: str):
    labs = dom.lower().split(".")
    return [l for l in labs if l and l not in DOMAIN_KEEP and not l.isdigit()]


REAL_TLDS = set("""com org net edu gov mil int io dev info biz co me uk de fr it es nl ch at be cn jp ru in ca au us eu dk se
no fi pl cz pt br mx ar tr kr tw hk sg il ir gr hu ro si sk lu ie nz za ua by kz th my id vn ph pk bd lk np
ae sa eg ma ng ke tz cl pe uy ve ec bo py cr cu gt hn ni pa do pr is ee lv lt hr rs ba mk al bg cy mt
xyz app ai tech online site store shop cloud page blog wiki network systems solutions works studio design
engineering science global world one top club live news media space website""".split())
PLATFORM_DOMAINS = {"github.com", "gitlab.com", "githubusercontent.com", "github.io", "bitbucket.org"}


def add_domain(reg: Registry, dom: str, ctx=ALL):
    dom = dom.strip(".").lower()
    if "." not in dom or dom.rsplit(".", 1)[-1] not in REAL_TLDS:
        return
    if dom in PLATFORM_DOMAINS or any(dom.endswith("." + p) for p in PLATFORM_DOMAINS):
        return
    reg.add("domain", dom, ctx=ctx, substr=True)
    for lab in domain_labels(dom):
        if lab in SOFTWARE:
            continue
        reg.add("domain", lab, ctx=ctx if len(lab) >= 4 else ("path", "text"))


def add_account(reg: Registry, owner: str):
    k = owner.lower().replace("-", "")
    if k in SOFTWARE:
        reg.add("account", owner, ctx=("path",))
        return
    reg.add("account", owner, ctx=ALL if len(owner) >= 4 else ("path", "text"), substr=len(owner) >= 5)


def add_project(reg: Registry, repo: str):
    if not repo:
        return
    lo = repo.lower()
    distinctive = (lo not in GENERIC and lo.replace("-", "") not in SOFTWARE and
                   (re.search(r"[-_0-9]", repo) or re.search(r"[a-z][A-Z]", repo) or len(repo) >= 8))
    reg.add("project", repo, ctx=ALL if distinctive else ("path",))


def build_seed_registry(manifest_paths) -> Registry:
    reg = Registry()
    for kind, text, ctx in CURATED:
        reg.add(kind, text, ctx=ctx)
    seen = set()
    for p in manifest_paths:
        a = p.split("/")
        if a[0] == "archives":
            seg = re.sub(r"\.(zip|tar|tgz|7z|rar|gz)$", "", a[1])
            key = ("gh", seg)
            if key in seen:
                continue
            seen.add(key)
            o, r, _ = split_owner_repo(seg)
            add_account(reg, o)
            add_project(reg, r)
            continue
        if len(a) < 3 or a[0] != "sources":
            continue
        kind, seg = a[1], a[2]
        if (kind, seg) in seen:
            continue
        seen.add((kind, seg))
        if kind == "github":
            o, r, _ = split_owner_repo(seg)
            add_account(reg, o)
            add_project(reg, r)
        elif kind == "gitlab":
            host, *rest = seg.split("__")
            rest = [x for x in rest if x]
            if rest:
                add_account(reg, rest[0])
                for x in rest[1:]:
                    if x != "project":
                        add_project(reg, x)
        elif kind == "web":
            add_domain(reg, seg)
        elif kind == "catalog":
            if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", seg):
                add_domain(reg, seg)
    return reg
