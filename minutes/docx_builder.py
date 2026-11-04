"""Builds the minutes .docx by cloning paragraphs from the template, so the
fonts, colors, spacing and numbered lists match the team's own document."""

import copy
import re

from docx import Document
from docx.oxml.ns import qn

from .textclean import ZWSP


def _text(p):
    return "".join(t.text or "" for t in p.iter(qn("w:t"))).replace(ZWSP, "").strip()


def _style(p):
    ps = p.find(qn("w:pPr") + "/" + qn("w:pStyle"))
    return ps.get(qn("w:val")) if ps is not None else None


def _num(p):
    n = p.find(qn("w:pPr") + "/" + qn("w:numPr"))
    if n is None:
        return None
    ilvl = n.find(qn("w:ilvl"))
    nid = n.find(qn("w:numId"))
    return (int(ilvl.get(qn("w:val"))) if ilvl is not None else 0,
            nid.get(qn("w:val")) if nid is not None else None)


def _key(s):
    return re.sub(r"[^a-z]", "", s.lower())


class Template:
    def __init__(self, path):
        self.doc = Document(str(path))
        self.body = self.doc.element.body
        paras = [el for el in self.body if el.tag == qn("w:p")]
        self.title = next((p for p in paras if _style(p) == "Title"), None)
        self.subtitle = next((p for p in paras if _style(p) == "Subtitle"), None)
        self.h1 = next((p for p in paras if _style(p) == "Heading1"), None)
        self.h2 = next((p for p in paras if _style(p) == "Heading2"), self.h1)
        plain = [p for p in paras if _style(p) is None and _num(p) is None and _text(p)]
        self.agenda = next((p for p in plain if _key(_text(p)) == "agenda"), self.h1)
        self.line = next((p for p in plain if ":" in _text(p)), plain[0] if plain else self.h1)
        # Per heading: the first level 0 and level 1 bullet under it.
        self.lists = {}
        heading = None
        for p in paras:
            if _style(p) in ("Heading1", "Heading2"):
                heading = _key(_text(p))
                continue
            n = _num(p)
            if n and heading:
                d = self.lists.setdefault(heading, {})
                d.setdefault(n[0], p)
        first = next((d for d in self.lists.values() if 0 in d), None)
        self.default_list = first if first is not None else {}
        self.numbering = self.doc.part.numbering_part.element
        self._next_num = max([int(n.get(qn("w:numId"))) for n in self.numbering.findall(qn("w:num"))] + [0]) + 1

    def list_protos(self, heading):
        d = self.lists.get(_key(heading)) or self.default_list
        def pick(*cands):
            return next((c for c in cands if c is not None), None)
        l0 = pick(d.get(0), self.default_list.get(0))
        l1 = pick(d.get(1), self.default_list.get(1), l0)
        return l0, l1

    def new_num(self, proto):
        """A fresh list instance so each section's numbering restarts at 1."""
        _, old = _num(proto)
        old_num = next(n for n in self.numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == old)
        abstract = old_num.find(qn("w:abstractNumId")).get(qn("w:val"))
        nid = str(self._next_num)
        self._next_num += 1
        num = self.numbering.makeelement(qn("w:num"), {qn("w:numId"): nid})
        a = num.makeelement(qn("w:abstractNumId"), {qn("w:val"): abstract})
        num.append(a)
        for lvl in range(2):
            o = num.makeelement(qn("w:lvlOverride"), {qn("w:ilvl"): str(lvl)})
            o.append(o.makeelement(qn("w:startOverride"), {qn("w:val"): "1"}))
            num.append(o)
        self.numbering.append(num)
        return nid


def _clone(proto, runs):
    """Copy a paragraph's formatting and replace its text.
    runs: list of (text, bold or None)."""
    p = copy.deepcopy(proto)
    # Copy the formatting of the run that holds the text (some paragraphs
    # start with an empty run or a line break run).
    runs_all = proto.findall(qn("w:r"))
    first_run = next((r for r in runs_all if r.find(qn("w:t")) is not None and (r.find(qn("w:t")).text or "").strip()),
                     runs_all[0] if runs_all else None)
    for child in list(p):
        if child.tag != qn("w:pPr"):
            p.remove(child)
    for text, bold in runs:
        r = copy.deepcopy(first_run) if first_run is not None else p.makeelement(qn("w:r"), {})
        for child in list(r):
            if child.tag != qn("w:rPr"):
                r.remove(child)
        rpr = r.find(qn("w:rPr"))
        if rpr is None:
            rpr = r.makeelement(qn("w:rPr"), {})
            r.insert(0, rpr)
        if bold is not None:
            for tag in ("w:b", "w:bCs"):
                old = rpr.find(qn(tag))
                if old is not None:
                    rpr.remove(old)
                rpr.insert(0, rpr.makeelement(qn(tag), {} if bold else {qn("w:val"): "0"}))
        t = r.makeelement(qn("w:t"), {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        t.text = text
        r.append(t)
        p.append(r)
    return p


def _set_list(p, num_id, ilvl, first, last):
    ppr = p.find(qn("w:pPr"))
    numpr = ppr.find(qn("w:numPr"))
    numpr.find(qn("w:ilvl")).set(qn("w:val"), str(ilvl))
    numpr.find(qn("w:numId")).set(qn("w:val"), num_id)
    sp = ppr.find(qn("w:spacing"))
    if sp is None:
        sp = ppr.makeelement(qn("w:spacing"), {})
        ppr.insert(list(ppr).index(numpr) + 1, sp)
    for a in list(sp.attrib):
        if "before" in a.lower() or "after" in a.lower():
            del sp.attrib[a]
    # Same rhythm as the template: no gaps inside a list, space after it.
    if not first:
        sp.set(qn("w:before"), "0")
        sp.set(qn("w:beforeAutospacing"), "0")
    if not last:
        sp.set(qn("w:after"), "0")
        sp.set(qn("w:afterAutospacing"), "0")
    ind = ppr.find(qn("w:ind"))
    if ind is not None:
        ind.set(qn("w:left"), str(720 * (ilvl + 1)))
        ind.set(qn("w:hanging"), "360")


def build(template_path, model, out_path):
    t = Template(template_path)
    sect = t.body.find(qn("w:sectPr"))
    for el in list(t.body):
        if el is not sect:
            t.body.remove(el)

    out = []
    out.append(_clone(t.title, [(model["title"], None)]))
    out.append(_clone(t.subtitle, [(model["subtitle"], None)]))
    out.append(_clone(t.h1, [("Attendees", None)]))
    for label, names in model["attendees"]:
        out.append(_clone(t.line, [(f"{label}: {names}", None)]))
    out.append(_clone(t.agenda, [("Agenda", None)]))

    for sec in model["body"]:
        proto_h = t.h1 if sec["style"] == "h1" else t.h2
        out.append(_clone(proto_h, [(sec["heading"], None)]))
        l0, l1 = t.list_protos(sec["heading"])
        num_id = t.new_num(l0)
        rows = []
        for item in sec["items"]:
            if item.get("bold"):
                runs = [(item["bold"], True), (" " + item["text"] if item["text"] else "", False)]
            else:
                runs = [(item["text"], False if l0 is not None else None)]
            rows.append((0, runs))
            for s in item.get("sub", []):
                rows.append((1, [(s, False)]))
        for i, (lvl, runs) in enumerate(rows):
            p = _clone(l0 if lvl == 0 else l1, runs)
            _set_list(p, num_id, lvl, i == 0, i == len(rows) - 1)
            out.append(p)

    for el in out:
        sect.addprevious(el)
    t.doc.save(str(out_path))
