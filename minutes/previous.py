"""Finds last week's minutes so the model can check last week's goals
against what was said this week. Reads the .docx in the output folder
(including any edits the team made by hand)."""

import glob
from pathlib import Path

from docx import Document

from .config import resolve
from .dates import find_long_date
from .textclean import ZWSP


def docx_text(path):
    """Plain text with simple bullets, for the model to read."""
    doc = Document(str(path))
    lines = []
    for p in doc.paragraphs:
        text = p.text.replace(ZWSP, "").strip()
        if not text:
            continue
        style = (p.style.name or "") if p.style is not None else ""
        numpr = p._p.pPr is not None and p._p.pPr.numPr is not None
        if style.startswith("Heading") or style == "Title":
            lines.append(f"\n## {text}")
        elif numpr:
            ilvl = p._p.pPr.numPr.ilvl
            level = int(ilvl.val) if ilvl is not None else 0
            lines.append("  " * level + "- " + text)
        else:
            lines.append(text)
    return "\n".join(lines).strip()


def meeting_date_of(path):
    try:
        doc = Document(str(path))
    except Exception:
        return None
    for p in doc.paragraphs[:6]:
        d = find_long_date(p.text)
        if d:
            return d
    return None


def find_previous(cfg, meeting_date):
    """The most recent minutes dated before this meeting."""
    candidates = []
    # Only files named like our minutes, since the output folder may be a
    # busy one such as Downloads.
    prefix = cfg["output"].get("filename", "Meeting Minutes_{MM}_{DD}").split("{")[0]
    folder = resolve(cfg["output"]["folder"])
    if folder.exists():
        candidates += [p for p in folder.glob(glob.escape(prefix) + "*.docx") if not p.name.startswith("~$")]
    # Each session keeps its own copy, in case the output file was moved or deleted.
    sessions = resolve(cfg["recording"]["sessions_dir"])
    if sessions.exists():
        candidates += list(sessions.glob("*/*.docx"))
    template = resolve(cfg["output"]["template"])
    if template.exists():
        candidates.append(template)  # the template is a real past meeting too
    best = None
    for p in candidates:
        d = meeting_date_of(p)
        if d and d < meeting_date and (best is None or d > best[0]):
            best = (d, p)
    if not best:
        return None
    text = docx_text(best[1])
    return {"date": best[0], "path": str(best[1]), "text": text, "sections": split_sections(text)}


def split_sections(text):
    """{"goals for next week": "- ...", ...} from docx_text output."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}
