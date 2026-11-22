"""Independent PII candidate detectors for PDF / text (used to estimate leaks of
identities the registry does not know, and to discover them)."""
from __future__ import annotations

import re

GIVEN = set("""
aaron abby adam adrian ahmed aidan alan albert alberto alejandro alessandro alex alexander alexandra alexis alfred
ali alice alicia allan allen alvaro amanda amber amelia amir amy ana andre andrea andreas andres andrew andy angel
angela angelo anita ann anna anne annie anthony antoine anton antonio arjun armando arnaud arthur ashley austin
barbara barry ben benjamin bernard bernd bert beth bill billy bob bobby boris brad bradley brandon brenda brett
brian bruce bruno bryan caleb camille carl carla carlos carmen carol caroline carter catherine charles charlie
chen chris christian christina christine christoph christopher claire claudia clement colin connor craig cristian
dan dana daniel daniela danny dario dave david dean debbie denis dennis derek diana diane diego dieter dimitri
dominik dominique don donald doug douglas duncan dylan ed eddie edgar edward elena eli elias elizabeth ellen emily
emma enrico eric erik erin ernesto ethan eugene eva evan fabian fabio felipe felix fernando filip florian francesco
francis francisco frank franz fred frederic gabriel gareth gary george georg gerald gerhard gianluca giorgio
giovanni giuseppe glen gloria gordon grace graham greg gregory guillaume gustavo hannah hans harry heather heinz
helen henry herbert hiroshi howard hugo ian igor isaac ivan jack jacob jake james jamie jan jane janet jason javier
jay jean jeff jeffrey jennifer jens jeremy jerome jerry jesse jessica jim jimmy joan joe joel johan johann john
johnny jon jonas jonathan jordan jorge jose joseph josh joshua juan judith julia julian julie julien jun justin
kai karen karl kate katherine kathy katie keith kelly ken kenneth kevin kim kirk klaus kurt kyle lars laura
lauren lawrence lee leo leon leonardo linda lisa liam lorenzo louis luc luca lucas lucia luis luke lynn maria
marc marcel marco marcus margaret maria marie mario mark marko martin mary mateo matt matteo matthew matthias max
maxime maximilian megan mehmet melissa michael michel michele michelle miguel mike mikhail mohamed mohammad
monica nancy natalie nathan neil nicholas nick nicola nicolas nikolai noah norbert olga oliver olivier omar oscar
pablo pamela pascal patricia patrick paul paula pavel pedro peter phil philip philipp philippe pierre pietro
rachel rafael ralf ralph randy raphael ray raymond rebecca reza ricardo richard rick riccardo rob robert roberto
robin rodrigo roger roland roman ron ronald rory ross ruben russell ryan sam samuel sandra sara sarah scott
sean sebastian sergei sergio seth shane sharon simon simone sophie stefan stefano stephan stephanie stephen steve
steven stuart susan sven taylor ted teresa thomas tim timothy tobias todd tom tomas tommy tony travis tyler uwe
valentin victor vincent vladimir walter wayne wei werner william wolfgang xavier yann yuki yusuf zach zhang
""".split())

INITIAL_SURNAME = re.compile(r"(?<![A-Za-z.])([A-Z])\.\s?([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'\-]{2,20}|[A-Z]{3,20})(?![A-Za-zÀ-ÖØ-öø-ÿ])")
GIVEN_SURNAME = re.compile(r"(?<![A-Za-z])([A-Z][a-zà-öø-ÿ]{1,14}|[A-Z]{2,14})\s([A-Z][a-zà-öø-ÿ'\-]{2,20}|[A-Z]{3,20})(?![A-Za-zÀ-ÖØ-öø-ÿ])")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{2,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,10}")
URL = re.compile(r"(?:https?://|www\.)[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
ORG = re.compile(r"(?<![A-Za-z])((?:[A-Z][A-Za-z0-9&'\-]+ ){0,3}[A-Z][A-Za-z0-9&'\-]+),? (?:Inc|LLC|Ltd|GmbH|Corp|Corporation|AG|S\.A|B\.V|SpA|Co\., Ltd)\.?(?![A-Za-z])")

_common = None


def _is_common(word: str) -> bool:
    global _common
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return False
    w = word.lower()
    return max(zipf_frequency(w, l) for l in ("en", "fr", "de", "es", "it")) >= 3.3


NOT_SURNAME = {"rev", "dwg", "pdf", "inc", "ltd", "llc", "ref", "fig", "see", "max", "min", "typ", "dia", "mm",
               "assy", "qty", "sheet", "scale", "date", "size", "mfg", "eng", "chk", "appd", "app", "drawn"}


def candidates(text: str):
    """(kind, string) person/org/contact candidates found in text."""
    out = set()
    ini = []
    for m in INITIAL_SURNAME.finditer(text):
        sur = m.group(2)
        if sur.lower() in NOT_SURNAME or _is_common(sur):
            continue
        ini.append(m)
    # lettered lists ("A. Serrez", "B. Apriete", "C. Retirez"): 3+ different
    # leading letters among A-F followed by mixed-case words -> list items
    listy = {m.group(1) for m in ini if m.group(1) in "ABCDEF" and not m.group(2).isupper()}
    for m in ini:
        if len(listy) >= 3 and m.group(1) in listy and not m.group(2).isupper():
            continue
        if "\n" in m.group(0):
            continue
        out.add(("person", m.group(0)))
    for m in GIVEN_SURNAME.finditer(text):
        g, s = m.group(1), m.group(2)
        if g.lower() in GIVEN and s.lower() not in NOT_SURNAME and not _is_common(s):
            out.add(("person", m.group(0)))
    for m in EMAIL.finditer(text):
        out.add(("email", m.group(0)))
    for m in ORG.finditer(text):
        out.add(("org", m.group(0)))
    return out
