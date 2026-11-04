"""Makes a short fake two-track meeting with the Mac's built-in voices, for
testing the pipeline without a real recording.

  .venv/bin/python tests/make_test_meeting.py OUT_DIR

Writes OUT_DIR/zoom.wav (people on Zoom), OUT_DIR/room.wav (people in the
room, plus a quiet copy of the Zoom audio like a real laptop mic picks up)
and prints when the Zoom call ended.
"""

import array
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

RATE = 16000

# (where, voice, line). "zoom" = remote, "room" = in person, "after" = room after Zoom ends.
SCRIPT = [
    ("room", "Daniel", "Hi everyone, thanks for joining. Just a reminder that this meeting is recorded and "
                       "transcribed. Let's start with our accomplishments from last week."),
    ("room", "Daniel", "We finished the research on ArcGIS and we started integrating it into the Technical "
                       "Support Directory map. We also created the wireframe for the Producer Network maps."),
    ("zoom", "Flo (English (US))", "That's great to hear. Did you get access to the ArcGIS credentials yet?"),
    ("room", "Eddy (English (US))", "Not yet. We emailed Ryan in IT on Friday and we are still waiting for the "
                                    "API key. That is our biggest blocker right now."),
    ("zoom", "Grandpa (English (US))", "This is Eric. I will reach out to Ryan today and make sure you get the "
                                       "API key by Monday."),
    ("room", "Daniel", "Thank you Eric. For sprint goal status, the producer network browsing feature is in "
                       "progress. Users can now browse networks next to the map. The mobile layout work is done."),
    ("room", "Eddy (English (US))", "One question for the sponsors. For the submission intake form, which "
                                    "fields do you want us to collect for each producer network?"),
    ("zoom", "Flo (English (US))", "Good question. We want the network name, a contact email, the counties "
                                   "they cover and a website link. I will send you the full list by Wednesday."),
    ("room", "Daniel", "Perfect. One risk we see is that the ArcGIS data format might not match our current "
                       "database structure, so the migration could take longer than planned."),
    ("zoom", "Grandpa (English (US))", "Let's set up a short call next week to go over the data structure "
                                       "together."),
    ("room", "Daniel", "Sounds good. For next week our goals are to finish the ArcGIS integration, start the "
                       "producer network detail page, and write tests for the map. Sprint 3 ends on October "
                       "28th. Thanks everyone, see you next Thursday."),
    ("zoom", "Flo (English (US))", "Thanks team, great work. Bye everyone."),
    ("after", "Eddy (English (US))", "Okay, the call ended. So who is taking the producer network detail page?"),
    ("after", "Daniel", "I can take the detail page. Can you write the map tests and also follow up with Eric "
                        "if we don't have the key by Monday?"),
    ("after", "Eddy (English (US))", "Yes, I will do the map tests. Let's also move our team stand up to "
                                     "Tuesday at five so everyone can make it."),
]


def tts(voice, text, out):
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(aiff), "-ar", str(RATE), "-ac", "1",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    with wave.open(str(out)) as w:
        return array.array("h", w.readframes(w.getnframes()))


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "test_meeting")
    out.mkdir(parents=True, exist_ok=True)
    zoom, room = array.array("h"), array.array("h")
    zoom_end = None
    with tempfile.TemporaryDirectory() as tmp:
        for i, (where, voice, text) in enumerate(SCRIPT):
            if where == "after" and zoom_end is None:
                zoom_end = len(zoom) / RATE + 5
                pad = array.array("h", [0]) * (RATE * 20)  # a pause after the call ends
                zoom.extend(pad)
                room.extend(pad)
            s = tts(voice, text, Path(tmp) / f"{i}.wav")
            gap = array.array("h", [0]) * int(RATE * 0.8)
            silent = array.array("h", [0]) * len(s)
            if where == "zoom":
                zoom.extend(s)
                room.extend(array.array("h", (v // 4 for v in s)))  # echo from the speakers
            else:
                room.extend(s)
                zoom.extend(silent)
            zoom.extend(gap)
            room.extend(gap)
    for name, data in (("zoom.wav", zoom), ("room.wav", room)):
        with wave.open(str(out / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(data.tobytes())
    m, s = divmod(int(zoom_end), 60)
    print(f"wrote {out}/zoom.wav and {out}/room.wav, Zoom ended at {m}:{s:02d}")


if __name__ == "__main__":
    main()
