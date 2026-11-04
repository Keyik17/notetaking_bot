"""Writes the minutes with Claude through the Claude Code command line
(`claude -p`), using the Claude plan you are signed in with. One call reads
the whole transcript and returns every section as JSON.

The transcript is sent to Anthropic. Set [llm] writer = "local" in
config.toml to keep everything on this Mac."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import attendee_groups
from .dates import short_date


def log(msg):
    print(f"[claude] {msg}", flush=True)


SYSTEM = (
    "You write official meeting minutes for a university senior design team and its project sponsors. "
    "You are accurate, clean and to the point. You only write what the transcript supports, in your own "
    "words, never as quotes. You reply only with JSON that matches the given schema."
)

GUIDE = """\
SECTIONS (JSON key: what goes in it)

accomplishments: Work the team finished since the last meeting, as reported in the meeting. Check each of last
  week's "Goals for next week" and include it only if the transcript shows it was done. Short past tense
  phrases, for example "Converted the Technical Support Directory map to ArcGIS". No work in progress, no plans,
  nothing that was only discussed. Leave "sub" empty.
sprint_goals: Each current sprint goal that was talked about, with "status" (Done, In progress, Not started or
  Blocked) and a one sentence "detail" on where it stands.
roadmap: The project timeline: where the team is, upcoming sprints and releases, and any roadmap changes that
  were requested or agreed, with details in "sub".
risks: Each risk (something that could go wrong later) as a short name in "text", with its status, mitigation
  plan and any update in "sub".
issues: Current problems or blockers (already happening). If none were reported, one bullet saying so, plus any
  feedback about what should count as an issue.
questions: Each real question about the project, the course or meeting logistics that someone asked, and how it
  was answered. "question" is a short topic title (2 to 8 words, not a quote, a name in parentheses if it is
  clear who asked). "answers" holds 1 to 4 short sentences: the answer, decision or follow up. Merge follow up
  questions on the same topic. Skip small talk, audio checks, greetings and anything unrelated to the project.
goals_next_week: What the team plans to do before the next meeting. Short phrases starting with a verb.
next_steps: Upcoming steps and dates for the team and the sponsors (for example "Eric will share the API
  documentation on Friday, October 30th"). One step per bullet, at most 8. Do NOT add the date of the next
  sponsor meeting; it is added automatically.
notes: 3 to 9 short facts or pieces of feedback someone who missed the meeting must know, including feedback
  from faculty after the sponsors left the call. Full sentences.
action_items: Who will do what. "owner" is a person's name when the transcript makes it clear, otherwise
  "Team" or "Sponsors". "task" includes the deadline if one was said.
after_call: What the team decided or planned in the room after the Zoom call ended: decisions, who will do
  what, concerns. 3 to 8 bullets in your own words. Ignore chat about classes, grades, other students, personal
  plans and anything not about the project. Empty list if nothing about the project was said.

RULES
- Use only facts from the transcript. Never invent names, dates or decisions.
- The transcript comes from speech recognition, so names and words are often misheard. Fix them using the
  PEOPLE list, the KNOWN MISHEARINGS below and common sense, but do not guess beyond that.
- Leave out private or personal details: email addresses, phone numbers, grades, gossip and personal plans.
- Be concise: each bullet under about 30 words, no filler such as "The team discussed".
- Write dates like "Thursday, November 5th". Never use em dashes or middle dots.
- Every key must be present. Use an empty list when nothing fits. Use [] for "sub" when there are no details.
"""


def _claude_bin(cfg):
    for p in (cfg["llm"].get("claude_bin"), shutil.which("claude"), str(Path.home() / ".local/bin/claude")):
        if p and Path(p).expanduser().exists():
            return str(Path(p).expanduser())
    raise RuntimeError("Claude Code (`claude`) not found. Install it or set [llm] claude_bin.")


def schema(sections):
    props = {s["key"]: s["schema"]["properties"]["items"] for s in sections}
    return {"type": "object", "properties": {"sections": {
        "type": "object", "properties": props, "required": list(props)}}, "required": ["sections"]}


def build_prompt(cfg, meeting_date, prev, main, after):
    people = "\n".join(f"{label}: {', '.join(names)}" for label, names in attendee_groups(cfg))
    prev_text = (prev or {}).get("text") or "(none available)"
    prev_date = prev["date"].isoformat() if prev else ""
    return (
        f"Write the minutes for the {cfg['meeting']['title']} held on {meeting_date.strftime('%A')}, "
        f"{short_date(meeting_date)}, {meeting_date.year}.\n\n"
        f"PEOPLE (correct spellings):\n{people}\n\n"
        f"KNOWN MISHEARINGS: {cfg['meeting'].get('misheard_names') or '(none)'}\n\n"
        "HOW TO READ THE TRANSCRIPT: lines marked 'On Zoom' are people on the Zoom call (usually sponsors and "
        "faculty), lines marked 'In the room' are people in the room (usually the student team). The labels do "
        "not say which person spoke. The start of the recording can include chat before the meeting begins.\n\n"
        + GUIDE +
        f"\nLAST WEEK'S MINUTES ({prev_date}), for the style and to check last week's goals:\n"
        f"<last_week>\n{prev_text}\n</last_week>\n\n"
        f"TRANSCRIPT DURING THE ZOOM CALL:\n<transcript>\n{main}\n</transcript>\n\n"
        f"TRANSCRIPT IN THE ROOM AFTER THE ZOOM CALL ENDED:\n<after_call>\n{after or '(nothing recorded)'}\n"
        "</after_call>\n\n"
        "Now write every section as JSON."
    )


def write(cfg, sections, meeting_date, prev, main, after):
    """Returns {key: items} for every section."""
    exe = _claude_bin(cfg)
    prompt = build_prompt(cfg, meeting_date, prev, main, after)
    cmd = [exe, "-p", "Follow the instructions above.",
           "--output-format", "json",
           "--json-schema", json.dumps(schema(sections)),
           "--system-prompt", SYSTEM,
           "--tools", "",
           "--setting-sources", "",
           "--no-session-persistence",
           "--model", str(cfg["llm"].get("claude_model", "opus"))]
    timeout = int(cfg["llm"].get("claude_timeout_seconds", 900))
    last = None
    for attempt in range(2):
        log(f"writing all sections with Claude ({cfg['llm'].get('claude_model', 'opus')})")
        # An empty folder as the working directory, so no project files or
        # instructions are picked up.
        with tempfile.TemporaryDirectory() as tmp:
            try:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=tmp)
            except subprocess.TimeoutExpired:
                last = f"no answer after {timeout}s"
                log(last)
                continue
        try:
            out = json.loads(r.stdout)
        except ValueError:
            last = (r.stderr or r.stdout).strip()[:300] or f"exit code {r.returncode}"
            log(f"failed: {last}")
            continue
        if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
            last = str(out.get("result") or out.get("subtype"))[:300]
            log(f"failed: {last}")
            continue
        u = out.get("usage", {})
        log(f"  {u.get('input_tokens', 0) + u.get('cache_creation_input_tokens', 0) + u.get('cache_read_input_tokens', 0)}"
            f" tokens in, {u.get('output_tokens', 0)} out, {out.get('duration_ms', 0) / 1000:.0f}s")
        return out["structured_output"].get("sections", {})
    raise RuntimeError(f"Claude could not write the minutes: {last}")
