"""Writes the meeting content with a local model, one focused pass per
section, then a merge pass. The code fills the fixed parts (title, date,
place, attendees, next meeting agenda) itself."""

import json
import re
from difflib import SequenceMatcher

from . import llm
from .config import attendee_groups
from .dates import short_date
from .textclean import clean, join_names


def log(msg):
    print(f"[notes] {msg}", flush=True)


# ---- JSON schemas -------------------------------------------------------

def _list_of(props, required):
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": props, "required": required}}},
        "required": ["items"],
    }


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}
BULLETS = _list_of({"text": STR, "sub": STRS}, ["text", "sub"])
GOALS = _list_of({"goal": STR, "status": STR, "detail": STR}, ["goal", "status", "detail"])
QUESTIONS = _list_of({"question": STR, "answers": STRS}, ["question", "answers"])
ACTIONS = _list_of({"owner": STR, "task": STR}, ["owner", "task"])

# ---- Sections -----------------------------------------------------------
# key, heading in the .docx, heading style, schema, what to ask the model.
# "part": which part of the transcript the section is about.

SECTIONS = [
    dict(key="accomplishments", heading="Accomplishments from our last meeting", style="h2",
         schema=BULLETS, part=1, prev=["goals for next week"], task=(
             "List what the team finished since the last meeting. Check each of LAST WEEK'S GOALS: include "
             "it (reworded in the past tense) only if the transcript says or clearly shows it was done. Also "
             "add other finished work the team reported. Do not include work that is still in progress, "
             "plans, promises or deadlines (anything that 'will' happen or is due 'by' a day), or things "
             "decided during this meeting. If nothing was reported as finished, return an empty list. Short phrases, for example "
             "'Gained GoDaddy Domain Access'. Leave 'sub' empty.")),
    dict(key="sprint_goals", heading="Sprint goal status", style="h2", schema=GOALS, part=1,
         prev=["sprint goal status"], task=(
        "List the status of the sprint goals whose progress was talked about in the transcript. When a goal "
        "matches one of LAST SPRINT GOALS, use that goal's name. Skip goals nobody mentioned; never copy a "
        "goal or its details from LAST SPRINT GOALS unless the transcript talks about it. 'detail' must "
        "only contain what the transcript says. 'status' is "
        "one of: Done, In progress, Not started, Blocked. 'detail' is one or two sentences on what was said, "
        "for example 'Researching implementation for the Technical Support Directory. Integration testing "
        "is pending access to ArcGIS credentials.'")),
    dict(key="roadmap", heading="Project Roadmap", style="h2", schema=BULLETS, part=1, task=(
        "List changes to the overall project roadmap (which features come in which sprint, the final "
        "timeline) that were explicitly discussed in the transcript. Put the change in 'text' and details "
        "in 'sub'. Plans for next week are not roadmap changes. Return an empty list if the roadmap was "
        "not discussed.")),
    dict(key="risks", heading="Risk", style="h2", schema=BULLETS, part=1, task=(
        "List risks to the project raised in the transcript (things that could go wrong later, technical or "
        "schedule). Short noun phrases, for example 'API/Data structure compatibility'. Tasks, plans and "
        "deadlines are not risks. Leave 'sub' empty.")),
    dict(key="issues", heading="Issues", style="h2", schema=BULLETS, part=1, task=(
        "List current problems or blockers raised in the transcript (things already going wrong or waiting on "
        "someone). Short noun phrases, for example 'ArcGIS Credentials Access'. Things that only might "
        "happen are risks, not issues. Tasks and plans are not issues. Leave 'sub' empty.")),
    dict(key="questions", heading="Questions", style="h2", schema=QUESTIONS, part=1, task=(
        "List the project questions that the team, the sponsors or the faculty asked each other, and how "
        "they were answered. QUESTIONS ASKED lists the sentences that end with a question mark; use them "
        "to find the real questions, but most of them are small talk and must be skipped: greetings, audio "
        "checks ('Can you hear me?'), chat before the meeting starts, classes, sports, weekend plans and "
        "anything not about the project, the meeting logistics or the course. Merge follow up questions on "
        "the same topic into one item. 'question' is a short topic title of 2 to 8 words in your own "
        "words, for example 'ArcGIS access' or 'Release meeting date', never a quote from the transcript. "
        "'answers' has one to four short sentences in your own words: what was asked, the answer, and any "
        "decision or follow up. If there was no answer, say it is still open. At most 8 items. Return an "
        "empty list if no project question was asked.")),
    dict(key="goals_next_week", heading="Goals for next week", style="h2", schema=BULLETS, part=1, task=(
        "List what the team plans to do before the next meeting, from the transcript. Short phrases starting with "
        "a verb, for example 'Finish research on ArcGIS'. Leave 'sub' empty.")),
    dict(key="next_steps", heading="Next Steps", style="h2", schema=BULLETS, part=1, task=(
        "List the next steps for the team and for the sponsors from the transcript, including deadlines and dates "
        "that were said (for example 'Sprint 2 ends next week (Oct 21st)'). One bullet per step, at most 8, "
        "each step only once. Leave 'sub' empty. Do not "
        "add a bullet about the date of the next sponsor meeting; that is added automatically.")),
    dict(key="notes", heading="Notes", style="h1", schema=BULLETS, part=1, task=(
        "Write two to six short notes with the key decisions and facts from the transcript that someone who "
        "missed the meeting must know. Full sentences. Leave 'sub' empty.")),
    dict(key="action_items", heading="Action Items", style="h1", schema=ACTIONS, part=1, task=(
        "List the action items from the transcript. 'owner' is who will do it: 'Team', 'Sponsors', a person's "
        "name, or a combination like 'Team and Eric'. 'task' is what they will do, with the deadline if "
        "one was said.")),
    dict(key="after_call", heading="After the call", style="h1", schema=BULLETS, part=2, task=(
        "This transcript is what the team said in the room after the Zoom call ended. Summarize it in two to "
        "six bullets in your own words, in the same style as meeting minutes: decisions, who will do what, "
        "concerns and plans. Do not quote the transcript and do not write 'In the room'. Use 'sub' only for "
        "short details. Return an empty list if nothing meaningful was said.")),
]

SYSTEM = (
    "You write official meeting minutes. You are careful and precise: you only write things that "
    "were actually said in the transcript, and you never invent names, dates or decisions. Write in the "
    "plain, concise style of the previous minutes. Never use em dashes. Reply only with JSON that "
    "matches the given schema."
)

RULES = (
    "Rules:\n"
    "- Use only facts from the transcript above.\n"
    "- Only name a person if their name is said in the transcript next to that point. Otherwise write "
    "'Team', 'Sponsors' or 'A team member'. Never guess who said something.\n"
    "- The transcript comes from speech recognition. Fix obviously misheard words and names using the "
    "PEOPLE list, but do not guess beyond that.\n"
    "- Keep each bullet short (under 30 words). No filler, no 'The team discussed'.\n"
    "- If nothing fits, return {\"items\": []}."
)


def _tokens(s):
    return len(s) // 3  # rough estimate, errs on the safe side


def context_block(cfg, meeting_date, transcript, what):
    people = "\n".join(f"{label}: {', '.join(names)}" for label, names in attendee_groups(cfg))
    return (
        f"MEETING: {cfg['meeting']['title']} held on {meeting_date.strftime('%A')}, "
        f"{short_date(meeting_date)}, {meeting_date.year}.\n\n"
        f"PEOPLE (correct spellings):\n{people}\n\n"
        "HOW TO READ THE TRANSCRIPT: Lines marked 'On Zoom' are people on the Zoom call (usually "
        "sponsors or faculty). Lines marked 'In the room' are people in the room (usually the student "
        "team). The labels do not say which person spoke.\n\n"
        f"TRANSCRIPT ({what}):\n{transcript}\n\n"
    )


PREV_TITLES = {"goals for next week": "LAST WEEK'S GOALS", "sprint goal status": "LAST SPRINT GOALS"}


def prev_block(prev, keys):
    """Only the parts of last week's minutes a pass needs. Giving the model
    the whole document makes it copy old items into this week."""
    if not keys:
        return ""
    out = []
    for k in keys:
        text = (prev or {}).get("sections", {}).get(k)
        out.append(f"{PREV_TITLES.get(k, k.upper())} (from the {prev['date'].isoformat()} minutes):\n{text}"
                   if text else f"{PREV_TITLES.get(k, k.upper())}: (none available)")
    return "\n\n".join(out) + "\n\n"


def condense(cfg, text, label, budget_tokens):
    """For very long meetings: shrink the transcript in slices so it fits
    the model's context, keeping every decision and fact."""
    if _tokens(text) <= budget_tokens:
        return text
    slices, cur = [], []
    size = max(2000, budget_tokens // 2)  # tokens per slice
    for line in text.splitlines():
        cur.append(line)
        if _tokens("\n".join(cur)) > size:
            slices.append("\n".join(cur))
            cur = []
    if cur:
        slices.append("\n".join(cur))
    log(f"{label}: transcript too long, condensing {len(slices)} slices")
    schema = {"type": "object", "properties": {"notes": STRS}, "required": ["notes"]}
    out = []
    for s in slices:
        r = llm.chat_json(cfg, SYSTEM,
                          "Condense this part of a meeting transcript into detailed notes. Keep every "
                          "decision, question, answer, status update, name, date, number and task. Keep "
                          "the [time] and 'On Zoom' / 'In the room' labels at the start of each note.\n\n"
                          + s, schema)
        out.extend(clean(n) for n in r.get("notes", []))
    return "\n".join(out)


def _clean_section(sec, items):
    kind = sec["schema"]
    res = []
    for it in items or []:
        if kind is GOALS:
            g, st, d = clean(it.get("goal")), clean(it.get("status")), clean(it.get("detail"))
            if g:
                res.append({"goal": g, "status": (st or "In progress").rstrip("."), "detail": d})
        elif kind is QUESTIONS:
            q = clean(it.get("question"))
            if q:
                res.append({"question": q, "answers": [clean(a) for a in it.get("answers", []) if clean(a)]})
        elif kind is ACTIONS:
            o, t = clean(it.get("owner")), clean(it.get("task"))
            if t:
                res.append({"owner": o or "Team", "task": t})
        else:
            t = clean(it.get("text"))
            if t:
                res.append({"text": t, "sub": [clean(s) for s in it.get("sub", []) if clean(s)]})
    return res


def _item_text(item):
    return " ".join(str(v) for v in item.values() if isinstance(v, str))


def _similar(a, b):
    na, nb = re.sub(r"[^a-z0-9 ]", "", a.lower()), re.sub(r"[^a-z0-9 ]", "", b.lower())
    return SequenceMatcher(None, na, nb).ratio() >= 0.8


BARE_NAME = re.compile(r"^\s*(a team member|the team|team|sponsors?|someone|[A-Z][\w'-]+(\s+[A-Z][\w'-]+){0,3})\.?\s*$", re.I)
FLAT = {"accomplishments", "goals_next_week", "next_steps", "notes"}


def simple_merge(draft):
    """Fast merge pass without the model: drops near-duplicate bullets
    inside a section, and risks that are repeated word for word as issues.
    Sections that are flat lists in the template get their sub bullets
    lifted up first (a small model likes to nest every step under one)."""
    out = {}
    for k, items in draft.items():
        if k in FLAT:
            flat = []
            for it in items:
                flat.append({"text": it["text"], "sub": []})
                flat.extend({"text": s, "sub": []} for s in it.get("sub", []))
            items = flat
        kept = []
        for it in items:
            if "sub" in it:
                subs = []
                for s in it["sub"]:
                    if BARE_NAME.match(s):
                        continue  # the model put an owner's name alone as a sub bullet
                    if not any(_similar(s, o) for o in subs):
                        subs.append(s)
                it = {**it, "sub": subs}
            if not any(_similar(_item_text(it), _item_text(o)) for o in kept):
                kept.append(it)
        out[k] = kept
    issues = [_item_text(i) for i in out.get("issues", [])]
    out["risks"] = [r for r in out.get("risks", []) if not any(_similar(_item_text(r), i) for i in issues)]
    return out


def llm_merge(cfg, draft):
    keys = [s["key"] for s in SECTIONS]
    schema = {"type": "object", "properties": {s["key"]: s["schema"]["properties"]["items"] for s in SECTIONS},
              "required": keys}
    user = (
        "Below are draft meeting minutes in JSON, one list per section. Clean them up:\n"
        "- When the same point appears in more than one section, keep it only where it fits best "
        "(but 'notes' and 'action_items' may overlap with other sections).\n"
        "- Merge near-duplicate bullets inside a section.\n"
        "- Make the wording short, consistent and professional. Never use em dashes.\n"
        "- Do not add facts and do not drop facts. Keep the same JSON structure and keys.\n\n"
        + json.dumps(draft, indent=1)
    )
    merged = llm.chat_json(cfg, SYSTEM, user, schema)
    out = {}
    for sec in SECTIONS:
        k = sec["key"]
        before = draft.get(k, [])
        after = _clean_section(sec, merged.get(k))
        # A small model sometimes drops content here. Keep the draft then.
        out[k] = after if len(after) >= max(1, round(0.6 * len(before))) or not before else before
    return out


GENERIC = {"team": "A team member", "sponsors": "A sponsor"}


def guard_names(cfg, sections, main, after):
    """A small model likes to guess who did what. Replace any attendee name
    that was never said in the matching transcript with a generic word."""
    out = {}
    for sec in SECTIONS:
        heard = (after if sec["part"] == 2 else main).lower()
        items = json.loads(json.dumps(sections.get(sec["key"], [])))
        for label, names in attendee_groups(cfg):
            generic = GENERIC.get(label.lower(), "Someone")
            for n in names:
                parts = [x for x in re.split(r"\s+", n) if len(x) > 2]
                if any(re.search(r"\b" + re.escape(x.lower()) + r"\b", heard) for x in parts):
                    continue
                pats = [re.escape(n)] + [re.escape(x) for x in parts]
                rx = re.compile(r"\b(" + "|".join(pats) + r")\b")

                def fix(v):
                    if isinstance(v, str):
                        return rx.sub(generic, v)
                    if isinstance(v, list):
                        return [fix(x) for x in v]
                    if isinstance(v, dict):
                        return {k: fix(x) for k, x in v.items()}
                    return v
                items = fix(items)
        out[sec["key"]] = items
    return ground(out, main)


STOP = set("the a an and or but of to in on for with at by from is are was were be been it this that "
           "these those we they he she you i our their its has have had will would can could should may "
           "might not no yet also about into than then so as do does did team sponsors sponsor".split())
PLAN = re.compile(r"\b(will|going to|plan(s|ned)? to|next week|tomorrow|by (mon|tues|wednes|thurs|fri|satur|sun)day)\b", re.I)


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2]


def _support(text, transcript_words):
    """Share of an item's words that also appear in the transcript (by
    the first 5 letters, so 'deployed' matches 'deployment')."""
    ws = _words(text)
    if not ws:
        return 1.0
    return sum(1 for w in ws if w[:5] in transcript_words) / len(ws)


def asked_questions(text):
    """Sentences in the transcript that end with a question mark."""
    body = re.sub(r"^\[[\d:]+\]\s*(On Zoom|In the room)\s*:\s*", "", text, flags=re.M)
    return [q.strip() for q in re.split(r"(?<=[.?!])\s+", body) if q.strip().endswith("?")]


def ground(out, main):
    """Drop what the transcript does not back up. A small model likes to
    copy last week's goals, turn plans into accomplishments and turn
    statements into questions."""
    tw = {w[:5] for w in _words(main)}
    asked = asked_questions(main)

    def ok(text, need=0.5):
        return _support(text, tw) >= need

    out["accomplishments"] = [i for i in out.get("accomplishments", [])
                              if not re.search(r"in progress|ongoing", i["text"], re.I)
                              and not re.match(r"\s*(discussed|talked about|reviewed questions)\b", i["text"], re.I)
                              and not PLAN.search(i["text"]) and ok(i["text"])]
    plans = [_item_text(i) for k in ("goals_next_week", "next_steps", "action_items") for i in out.get(k, [])]

    def repeats(text, others):
        w = {x[:5] for x in _words(text)}
        return w and any(len(w & {x[:5] for x in _words(o)}) >= 0.6 * len(w) for o in others)

    # A plan said in this meeting is not an accomplishment.
    out["accomplishments"] = [i for i in out["accomplishments"] if not repeats(i["text"], plans)]
    # The roadmap is only for changes not already listed elsewhere.
    others = plans + [_item_text(i) for i in out["accomplishments"]]
    out["roadmap"] = [i for i in out.get("roadmap", []) if not repeats(i["text"], others)]
    out["sprint_goals"] = [g for g in out.get("sprint_goals", []) if g["detail"] and ok(g["detail"], 0.6)]
    out["questions"] = [q for q in out.get("questions", []) if len(q["question"].split()) <= 12]
    out["questions"] = [q for q in out["questions"]
                        if any(len(set(_words(q["question"] + " " + " ".join(q["answers"])))
                                   & set(_words(a))) >= 2 for a in asked)]
    risks = [r for r in out.get("risks", []) if not PLAN.search(r["text"])]
    issues = []
    for i in out.get("issues", []):
        if PLAN.search(i["text"]):
            continue
        if re.search(r"\b(might|could|may)\b", i["text"], re.I):
            risks.append(i)  # only a possibility, so it is a risk
        else:
            issues.append(i)
    out["risks"], out["issues"] = risks, issues
    for a in out.get("action_items", []):
        parts = re.split(r"\s*(?:,|\band\b)\s*", a["owner"])
        parts = [{"a team member": "Team", "a sponsor": "Sponsors", "someone": "Team"}.get(p.lower(), p)
                 for p in parts if p]
        parts = list(dict.fromkeys(parts))
        a["owner"] = join_names(parts) or "Team"
    for k in ("roadmap", "next_steps"):
        for it in out.get(k, []):
            it["sub"] = [x for x in it["sub"] if ok(x, 0.75)]
    return out


def names_heard(cfg, text):
    """People from the config whose first or last name comes up in the
    transcript. Only a hint for the log; the config list stays the default."""
    heard = []
    low = text.lower()
    for _, names in attendee_groups(cfg):
        for n in names:
            parts = [x for x in re.split(r"[\s-]+", n) if len(x) > 2]
            if any(re.search(r"\b" + re.escape(x.lower()) + r"\b", low) for x in parts):
                heard.append(n)
    return heard


def write_minutes(cfg, meeting_date, prev, main, after):
    if str(cfg["llm"].get("writer", "local")).lower() == "claude":
        from . import claude_writer
        try:
            raw = claude_writer.write(cfg, SECTIONS, meeting_date, prev, main, after)
            draft = {sec["key"]: _clean_section(sec, raw.get(sec["key"])) for sec in SECTIONS}
            if not after.strip():
                draft["after_call"] = []
            return {"sections": simple_merge(draft), "draft": draft, "failed": [], "writer": "claude",
                    "attendee_suggestions": names_heard(cfg, main + "\n" + after)}
        except Exception as e:
            log(f"Claude failed, using the local model instead: {e}")
    return _write_local(cfg, meeting_date, prev, main, after)


def _write_local(cfg, meeting_date, prev, main, after):
    llm.ensure_ready(cfg)
    num_ctx = int(cfg["llm"].get("num_ctx", 32768))
    budget = num_ctx - 4500
    main = condense(cfg, main, "during call", budget)
    after = condense(cfg, after, "after call", budget)
    # The transcript goes first so Ollama can reuse it between passes.
    ctx_main = context_block(cfg, meeting_date, main, "during the Zoom call")
    ctx_after = context_block(cfg, meeting_date, after, "in the room after the Zoom call ended")

    draft = {}
    failed = []
    for sec in SECTIONS:
        text, ctx = (after, ctx_after) if sec["part"] == 2 else (main, ctx_main)
        if not text.strip():
            draft[sec["key"]] = []
            continue
        log(f"writing '{sec['heading']}'")
        extra = ""
        if sec["key"] == "questions":
            asked = asked_questions(text)
            if not asked:
                draft[sec["key"]] = []
                continue
            extra = "QUESTIONS ASKED:\n" + "\n".join(f"- {q}" for q in asked) + "\n\n"
        user = ctx + prev_block(prev, sec.get("prev")) + extra + "TASK:\n" + sec["task"] + "\n\n" + RULES
        try:
            r = llm.chat_json(cfg, SYSTEM, user, sec["schema"])
            draft[sec["key"]] = _clean_section(sec, r.get("items"))
        except Exception as e:  # one bad section must not lose the whole minutes
            log(f"could not write '{sec['heading']}', leaving it for a person: {e}")
            draft[sec["key"]] = []
            failed.append(sec["key"])

    final = simple_merge(guard_names(cfg, draft, main, after))
    if str(cfg["llm"].get("merge_pass", "simple")).lower() == "llm" and any(final.values()):
        log("merge pass (model)")
        try:
            final = guard_names(cfg, llm_merge(cfg, final), main, after)
        except Exception as e:  # keep the draft if the merge fails
            log(f"merge pass failed, keeping draft: {e}")
    return {"sections": final, "draft": draft, "failed": failed, "attendee_suggestions": names_heard(cfg, main + "\n" + after)}


def doc_model(cfg, meeting_date, started_at, written, next_date):
    """Everything the .docx builder needs, in order."""
    from .dates import long_date, time_label
    s = written["sections"]
    body = []

    def bullets(items):
        return [{"text": i["text"], "sub": i.get("sub", [])} for i in items]

    for sec in SECTIONS:
        k = sec["key"]
        if k == "sprint_goals":
            items = [{"bold": f"{i['goal']}: {i['status']}.", "text": i["detail"], "sub": []} for i in s[k]]
        elif k == "questions":
            items = [{"text": i["question"], "sub": i["answers"]} for i in s[k]]
        elif k == "action_items":
            items = [{"text": f"{i['owner']}: {i['task']}", "sub": []} for i in s[k]]
        else:
            items = bullets(s[k])
        if k == "next_steps":
            items.append({"text": cfg["meeting"]["next_meeting_line"].format(date=short_date(next_date)), "sub": []})
        if k == "after_call":
            # The fixed agenda comes before "After the call".
            body.append({"heading": "Next Meeting Agenda", "style": "h1",
                         "items": [{"text": t, "sub": []} for t in cfg["next_meeting_agenda"]["items"]]})
            if not items:
                items = [{"text": "No discussion was recorded after the call.", "sub": []}]
        if k in written.get("failed", []):
            items = [{"text": "Could not be written automatically. Please fill in from the transcript.", "sub": []}]
        elif not items:
            items = [{"text": "None this week.", "sub": []}]
        body.append({"heading": sec["heading"], "style": sec["style"], "items": items})

    return {
        "title": cfg["meeting"]["title"],
        "subtitle": f"{long_date(meeting_date)} / {time_label(cfg, started_at)} / {cfg['meeting']['location']}",
        "attendees": [(label, join_names(names)) for label, names in attendee_groups(cfg)],
        "body": body,
    }
