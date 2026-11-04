# Where we left off (2026-11-03, evening)

Read this first in the next session.

## Goal

A Mac app that records the sponsor meeting and saves the minutes as a PDF,
with nobody needing setup. Rules from Keyik:

- **Completely free.** No Claude, no paid API. Writing uses Google Gemini's
  free tier, with the key built into the app.
- **Manual Start/Stop** from the menu bar. No auto-start, no stop on silence.
  Safety cap of 4 hours.
- **PDF only** (plus a transcript .txt) saved to ~/Downloads. No .docx.
- Keyik leaves after this semester. Mac teammates take over and must not
  need GitHub, Terminal, or any account. They copy the app and open it.
- Meeting content is public, so sending the transcript to Google is fine.

## How it works now

One self-contained Swift app in `recorder/` (no Python at runtime):

1. Records the Zoom audio and the room mic (`Capture.swift`, `ChunkWriter.swift`).
2. Transcribes with macOS 26 built-in speech recognition (`Transcriber.swift`).
3. Gemini writes every section as JSON (`Writer.swift`). Model
   `gemini-flash-latest`, backup `gemini-flash-lite-latest`.
4. The app draws the PDF in the template's style (`MinutesPDF.swift`).
5. `Pipeline.swift` runs the steps, retries unfinished recordings at launch,
   and keeps last week's minutes in
   `~/Library/Application Support/MeetingMinutes/history/` (seeded with the
   corrected 10/29 minutes).

Settings: `~/Library/Application Support/MeetingMinutes/settings.json`
(defaults in `recorder/Resources/default_settings.json`).
Build: `scripts/build_app.sh` makes `dist/MeetingMinutes.app` and
`dist/MeetingMinutes.zip` and installs to ~/Applications. It registers itself
to open at login. The old launchd jobs are removed.

## Done and tested

- The 10/29 transcript, then the writer, then the PDF. The layout matches
  the template.
- A fake 3-minute meeting from audio to PDF: built-in transcription took 4
  seconds, about 51 seconds in total (tested with Claude).
- **Gemini works.** The key is in `gemini_api_key.txt` and built into the
  app. The 10/29 test made a good 3-page PDF. `gemini-flash-latest` was
  overloaded (it hung, then timed out), so the backup
  `gemini-flash-lite-latest` wrote it in 9 seconds. The timeout is now 5
  minutes instead of 10. Older names like `gemini-2.5-flash` no longer work
  for new keys; the `-latest` names do.
- README now says free Gemini and `gemini_api_key.txt`.
- Rebuilt; `dist/MeetingMinutes.zip` is current.

Testing tip: in zsh, write `${m}:generateContent`, not `$m:generateContent`
(zsh reads `:g` as a modifier and you get fake 404s).

## Next steps

1. On Keyik's Mac, turn Screen Recording back on for MeetingMinutes (the
   rebuild reset it), then quit and reopen the app.
2. Put `dist/MeetingMinutes.zip` in the team's shared folder.
3. Try it on a real meeting (Thursday). Check the PDF and `pipeline.log`.
4. Later: delete the old Python version (`minutes/`, `config.toml`,
   `scripts/setup.sh`, `models/` (1.6 GB), `.venv`), and uninstall
   Homebrew's whisper-cpp and Ollama, after the app works on a real meeting.

## Limits to tell the team

- Needs macOS 26 (Tahoe) or newer.
- First open: System Settings > Privacy & Security > **Open Anyway** (the
  app isn't from the App Store), then allow Microphone and Screen Recording.
- The built-in speech recognition hasn't been tried on a real meeting yet.
- Gemini's free tier can change. Failed runs retry the next time the app
  starts.
