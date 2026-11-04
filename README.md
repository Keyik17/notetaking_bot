# Meeting Minutes

A Mac menu bar app that records the weekly sponsor meeting and saves the
minutes as a PDF in the team's format. Click **Start Recording** when the
meeting starts and **Stop Recording** when you're done. A few minutes later
`Meeting Minutes_MM_DD.pdf` is in your Downloads folder.

No bot joins Zoom. Nothing to install besides the app, no account, and it's
free: the minutes are written by Google's Gemini on its free tier, with the
team's key built into the app.

## Install (once)

1. Get `MeetingMinutes.zip` from the team's shared folder and double click it.
2. Drag **MeetingMinutes** into your Applications folder and open it.
3. macOS says it can't check the app (it is free and not from the App Store).
   Open **System Settings > Privacy & Security**, scroll down, click
   **Open Anyway**.
4. Allow **Microphone** and **Screen Recording** when asked. (Screen
   Recording is how macOS lets an app hear the Zoom audio. No video is kept.)

A waveform icon appears in the menu bar. The app starts by itself whenever
the Mac starts.

Needs macOS 26 (Tahoe) or newer and an internet connection when the minutes
are written.

## Each meeting

- Say at the start that the meeting is recorded and transcribed.
- Put the Mac in the middle of the table, so the room is clear.
- Menu bar icon > **Start Recording**. The icon turns red.
- After the meeting, and after the team's chat in the room, click
  **Stop Recording**. The PDF and a transcript appear in Downloads.
- Skim the PDF before sending it.

If you forget to stop, it stops on its own after 4 hours.

## How it works

1. Records two tracks: the Mac's audio (everyone on Zoom) and the
   microphone (everyone in the room), in 2 minute pieces so a crash loses
   little. It notes when Zoom closed; what's said after that goes under
   "After the call".
2. Transcribes with macOS's built-in speech recognition, on the Mac.
3. Sends the transcript and last week's minutes to Google's Gemini, which
   writes each section in the team's format. If the main model is busy, it
   waits and then uses a lighter backup model.
4. Saves the PDF and the transcript, then deletes the audio.

If there's no internet when you stop, it tries again the next time the app
starts.

## Settings

Menu bar icon > **Edit Settings** opens `settings.json`: attendees, meeting
day and time, location, next meeting agenda, where the PDF is saved
(`output.folder`), and how speech recognition mishears names
(`misheard_names`). Quit and reopen the app after editing.

## Costs

Free. Gemini's free tier is plenty for one meeting a week. The key is managed
at aistudio.google.com (API keys). Google can change the free tier; if
writing fails, the app tries again the next time it starts, and
**Redo Minutes for Last Recording** tries again right away.

## Problems

- **No Zoom audio in the transcript:** turn on MeetingMinutes in System
  Settings > Privacy & Security > Screen & System Audio Recording, then
  quit and reopen the app.
- **No PDF:** menu bar icon > **Open Recordings Folder**, open the newest
  folder and read `pipeline.log`. **Redo Minutes for Last Recording** tries
  again.
- App log: `~/MeetingMinutes/logs/app.log`.

## For whoever maintains it

The app is in `recorder/` (Swift). To build it:

1. Put the team's Gemini API key in `gemini_api_key.txt` at the top of this
   folder (never commit it; it's in `.gitignore`). In AI Studio, click
   **Copy key**, then run `pbpaste | tr -d '[:space:]' > gemini_api_key.txt`.
2. Run `scripts/build_app.sh`. It makes `dist/MeetingMinutes.app` and
   `dist/MeetingMinutes.zip` (the file to share) and installs a copy.

Without a key file, the app can't write minutes. A key can also be put in
`settings.json` under `gemini.api_key`.

The team's defaults (attendees, schedule, location) are in
`recorder/Resources/default_settings.json`, including the Gemini models
(`gemini.model` and `gemini.backup_model`). The instructions Gemini follows
are in `recorder/Sources/MeetingMinutes/Writer.swift`.

`minutes/`, `config.toml`, `scripts/setup.sh` and `models/` are the old
Python version and are not used by the app.
