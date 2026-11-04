# Meeting Minutes App: Project Brief

Hand this file to a new Claude Code session to start the build.

## Goal

A free Mac app that records our weekly sponsor meeting, transcribes it and
writes the minutes in our team's existing format, with nobody pressing a
button.

## Who and why

- Team: UNL CSE senior design team building the Nebraska Strategic Ag
  Coalition (NSAC) website. Six students: Barzan Mustafa, Clayton Howe, Keyik
  Annagulyyeva, Oliver Luebcke, Trang Tran, Vrisheek Venkatesh Goud Panjala.
- Weekly sponsor meeting: Thursday 2 PM to about 3 PM, hybrid. Some people
  are in person (Adele Learning Commons, Room 130) and some join on Zoom.
- Today someone writes the minutes by hand. Example of the format:
  `/Users/keyik/Downloads/Copy of Nebraska Strategic Ag Coalition - Meeting Minutes_10_15.docx`
  (read it with `textutil -convert txt -stdout "<path>"`).

## Hard requirements

1. **Fully free.** No paid APIs, subscriptions or services. That includes the
   Claude API and Claude Code. Every step runs on the Mac.
2. **No bot joins Zoom.** The app records the Mac's own audio.
3. **Records after Zoom ends.** The team keeps talking in the room after the
   call. Recording continues until the room goes quiet, and that part goes in
   its own "After the call" section.
4. **Fully automatic.** It starts, stops, transcribes, writes and saves on
   its own.
5. **Output in our exact format** as a .docx.
6. **Private.** Audio and transcripts never leave the Mac, except the
   finished .docx going to the team's shared folder.

## The machine

- Keyik's MacBook: Apple M5, 16 GB memory, about 180 GB free disk.
- `ffmpeg` is installed (Homebrew). Ollama and whisper.cpp are not yet.
- Only this Mac records. Two teammates use Windows; they only read the shared
  folder.

## How it works

1. **Start**
   - Primary trigger: a schedule (Thursday 1:58 PM) through a launchd agent.
   - Secondary trigger: when the Zoom app starts a meeting.
   - Keep the Mac awake while recording (`caffeinate`).
2. **Record two tracks**
   - **System audio** (everyone on Zoom) through ScreenCaptureKit, audio
     only. Needs the Screen Recording permission once.
   - **Microphone** (everyone in the room) through AVAudioEngine.
   - Two separate tracks give free speaker labels: "On Zoom" vs "In the
     room". Whisper has no speaker detection of its own.
   - Write audio to disk in chunks so a crash loses at most a few minutes.
3. **Stop** after about 5 minutes of silence on both tracks, with a hard cap
   (for example 2 hours). Mark the moment Zoom closed; everything after it is
   "After the call".
4. **Transcribe** with whisper.cpp on the Mac, using Metal and the
   `large-v3-turbo` model, with timestamps. An hour of audio should take a
   few minutes on the M5.
5. **Write the minutes** with Ollama running a free 7 to 8B model (try
   `qwen3:8b` or `llama3.1:8b`).
   - Set a large context (`num_ctx` 32k or more); the default is too small
     for an hour of transcript.
   - A small model does better with several focused passes than one big
     prompt: one pass per section (questions, action items, risks, and so
     on), then a merge pass.
   - The model only writes meeting content. The code fills the fixed parts
     itself: title, date, time, place, attendees, next meeting agenda.
   - Feed it last week's minutes. Last week's "Goals for next week" become
     this week's "Accomplishments", so the model checks them against what
     was said.
6. **Save**
   - Build the .docx in the same layout as the example (python-docx or
     similar).
   - Copy it to a Google Drive folder synced by Google Drive for desktop
     (free), named like `Meeting Minutes_MM_DD.docx`.
   - Keep the transcript next to it as a .txt so people can check the notes.
   - Delete the raw audio after a successful run (setting, default on).

## Minutes template (from the 10/15 doc)

```
Sponsor Meeting Minutes
<MONTH DAYth, YEAR> / 2 PM / Zoom and in person, Adele Learning Halls, Room 130
Attendees
  Team: ...
  UNL Faculty and Staff: ...
  Sponsors: ...
Agenda
Accomplishments from our last meeting   (bullets)
Sprint goal status                      (goal: status. detail)
Project Roadmap
Risk
Issues
Questions                               (question, then answer bullets under it)
Goals for next week
Next Steps
Notes
Action Items                            ("Owner: task")
Next Meeting Agenda                     (fixed list)
After the call                          (new: what the team said after Zoom ended)
```

Usual attendees from the 10/15 meeting:

- Faculty and staff: Melanie Kugler-Wright, Kang-il Park, Ben Ferguson.
- Sponsors: Alexa Davis, Rachael Whitehair, Nick Arneson, Eric Dinger, Sarah
  Starostka.

Keep these in a config file the user can edit, not in code. The model may
suggest who was present from the transcript, but the user's list is the
default.

## Suggested stack (the new session can change this with a reason)

- **Recorder:** a small Swift menu bar app (ScreenCaptureKit and AVAudioEngine
  need native code). Shows a red dot while recording, plus Start and Stop
  buttons for manual use.
- **Pipeline:** a script (Python or Swift) that the recorder calls when it
  stops. It runs whisper.cpp, then Ollama, then builds the .docx.
- **Scheduling:** a launchd agent.
- **Config:** one file holding the meeting schedule, attendees, template path,
  output folder, silence timeout and model name.

## Repo

- A new, separate repo, for example `~/Developer/meeting-minutes`. It must not
  go inside the NSAC website repo (`~/Developer/nsac`).
- Keep it general: the template, attendees and schedule come from config, so
  other teams could use it later.
- The README must state that everything is free and local, list the setup
  steps, and explain the macOS permissions (Microphone, Screen Recording).

## Phases

1. **Pipeline first, no recorder.** Feed an existing audio file through
   whisper.cpp, Ollama and the .docx builder. Compare the output with the
   10/15 doc.
2. **Recorder.** Two-track capture, manual Start and Stop, chunked files.
3. **Automation.** Schedule, Zoom detection, silence stop, Drive save.
4. **Real meeting test** on a Thursday, then fix what's off.
5. **Later, optional:** a settings screen and an installer for other users.
   Without Apple's paid developer account, users right-click and choose Open
   the first time. That's acceptable.

## Done when

- [ ] On Thursday at 2 PM, recording starts with nobody touching the Mac.
- [ ] Both Zoom audio and room audio are in the transcript, labelled by track.
- [ ] Talk after Zoom closes appears under "After the call".
- [ ] Recording stops on its own after the room goes quiet.
- [ ] A .docx in the 10/15 layout appears in the shared Drive folder within
      about 15 minutes of the stop.
- [ ] Nothing paid is used, and nothing but the .docx and .txt leaves the Mac.
- [ ] A crash mid-meeting still leaves usable audio on disk.

## Already decided against

- **Zoom bots** (Otter, Fireflies, Fathom, tl;dv): a bot joins the call, free
  tiers are capped, and they don't use our format.
- **Granola**: limited free tier, cloud storage.
- **Anarlog (formerly Hyprnote)**: free and local but exports Markdown, not our
  format, and the product has been renamed twice. Still useful as a reference
  for how a no-bot recorder works.
- **Claude API or Claude Code for the notes**: not free. (Changed 2026-11-03:
  the team now uses Claude Code on an existing Claude plan to write the
  minutes, because the local model's drafts needed too much fixing. The
  meeting content is public, so sending the transcript to Anthropic is fine.
  The local model stays as the backup.)
- **Meta's Muse over SSH or Tailscale**: free tier is capped, sends sponsor
  meetings to Meta's cloud, and gives an agent a shell on a laptop that holds
  the NSAC GitHub, Convex and Cloudflare credentials.
- **A web app**: a browser can't capture the Zoom app's audio in the
  background, and hosting Whisper or a model costs money.

## Things to tell people

- Announce at the start of each meeting that it's recorded and transcribed,
  including the talk after the call ends.
- Place the Mac in the middle of the table, or use a USB speakerphone, so the
  room audio is clear.

## Writing rules

No em dashes and no middle dots anywhere (code, comments, docs, commits). No
AI attribution in commits. This is coursework submitted under the team's own
names.
