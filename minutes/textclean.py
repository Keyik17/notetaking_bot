"""Small text fixes applied to everything the model writes."""

import re

EM = "\u2014"
EN = "\u2013"
MIDDOT = "\u00b7"
ZWSP = "\u200b"


def clean(s):
    if s is None:
        return ""
    s = str(s).replace(ZWSP, "")
    s = re.sub(r"\s*" + EM + r"\s*", ", ", s)
    s = s.replace(EN, "-")
    s = re.sub(r"\s*" + MIDDOT + r"\s*", ", ", s)
    s = re.sub(r"\*\*|__", "", s)          # stray markdown bold
    s = re.sub(r"^\s*[-*•]\s+", "", s)  # stray bullet characters
    s = re.sub(r"^\s*(\[[\d:]+\]\s*)?(In the room|On Zoom)\s*:\s*", "", s, flags=re.I)  # transcript labels
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r",\s*,", ",", s)
    return s


def join_names(names):
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]
