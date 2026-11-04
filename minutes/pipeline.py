"""Session folder in, minutes .docx and transcript .txt out.

A session folder (written by the recorder, or made by `from-audio`) holds:
  session.json   start/stop times, when Zoom started and ended (epoch seconds)
  chunks.jsonl   one line per audio chunk: {"track", "file", "start"}
  audio/         the chunk files
"""

import datetime as dt
import json
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from . import docx_builder, notes, pdf, transcribe
from .config import resolve
from .dates import file_stem, next_meeting, short_date
from .previous import find_previous


def log(msg):
    print(f"[pipeline] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def notify(text):
    """A local macOS notification. Nothing is sent anywhere."""
    text = text.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Meeting Minutes"'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_session(d):
    p = Path(d) / "session.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_session(d, data):
    p = Path(d) / "session.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


def update_session(d, **kw):
    s = read_session(d)
    s.update(kw)
    write_session(d, s)
    return s


def read_chunks(d):
    tracks = {"system": [], "mic": []}
    p = Path(d) / "chunks.jsonl"
    if not p.exists():
        return tracks
    for line in p.read_text().splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue  # a half written last line after a crash
        f = Path(c["file"])
        f = f if f.is_absolute() else Path(d) / f
        if f.exists() and f.stat().st_size > 0:
            tracks.setdefault(c["track"], []).append((str(f), float(c["start"])))
    return tracks


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def output_folder(cfg):
    folder = resolve(cfg["output"]["folder"])
    if folder.exists() or folder.parent.exists():
        folder.mkdir(parents=True, exist_ok=True)
        return folder, False
    fallback = Path("~/MeetingMinutes/output").expanduser()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, True


def run(cfg, session_dir, date_override=None, force=False, out_dir=None):
    d = Path(session_dir).resolve()
    s = read_session(d)
    lock = d / "processing.lock"
    if lock.exists() and pid_alive(lock.read_text().strip()):
        log("already being processed")
        return None
    lock.write_text(str(os.getpid()))
    try:
        update_session(d, status="processing", attempts=int(s.get("attempts", 0)) + 1)
        result = _run(cfg, d, date_override, force, out_dir)
        return result
    except Exception as e:
        traceback.print_exc()
        update_session(d, status="failed", error=str(e)[:500])
        notify(f"Minutes failed: {str(e)[:120]}. The audio was kept.")
        raise
    finally:
        lock.unlink(missing_ok=True)


def _run(cfg, d, date_override, force, out_dir):
    s = read_session(d)
    started = dt.datetime.fromtimestamp(s["started_at"]) if s.get("started_at") else None
    meeting_date = date_override or (started.date() if started else dt.date.today())
    t0 = s.get("started_at") or 0.0
    work = d / "work"
    log(f"meeting date {meeting_date.isoformat()}")

    # 1. Transcribe (cached)
    tj = work / "transcript.json"
    if tj.exists() and not force:
        segments = json.loads(tj.read_text())["segments"]
    else:
        tracks = read_chunks(d)
        if not any(tracks.values()):
            raise RuntimeError("no audio found in session")
        vocab = cfg["meeting"].get("vocabulary", "")
        names = ", ".join(n for g in cfg.get("attendees", []) for n in g.get("names", []))
        prompt = f"Weekly sponsor meeting. {names}. {vocab}."
        segments = transcribe.transcribe_tracks(cfg, tracks, work, prompt)
        tj.write_text(json.dumps({"segments": segments}, indent=1))
    after_start = transcribe.find_after_start(segments, s.get("zoom_ended_at"))
    main, after = transcribe.split(segments, t0 or (segments[0]["start"] if segments else 0), after_start)

    words = len((main + " " + after).split())
    log(f"transcript: {len(segments)} segments, {words} words")
    if words < 40:
        update_session(d, status="empty")
        notify("Recording had no speech, so no minutes were written.")
        log("too little speech, stopping")
        return None

    # 2. Write the minutes (cached)
    prev = find_previous(cfg, meeting_date)
    log(f"last week's minutes: {prev['path'] if prev else 'none'}")
    mj = work / "minutes.json"
    if mj.exists() and not force:
        written = json.loads(mj.read_text())
    else:
        written = notes.write_minutes(cfg, meeting_date, prev, main, after)
        mj.write_text(json.dumps(written, indent=1))
    if written.get("failed"):
        log("sections to fill in by hand: " + ", ".join(written["failed"]))
    if written.get("attendee_suggestions"):
        log("names heard in the transcript (attendee list not changed): " + ", ".join(written["attendee_suggestions"]))

    # 3. Build the .docx and the transcript .txt
    model = notes.doc_model(cfg, meeting_date, started, written, next_meeting(meeting_date, cfg["meeting"]["weekday"]))
    stem = file_stem(cfg, meeting_date)
    local_docx = d / f"{stem}.docx"
    docx_builder.build(resolve(cfg["output"]["template"]), model, local_docx)
    local_txt = d / f"{stem} transcript.txt"
    header = (f"{cfg['meeting']['title']}: transcript\n{short_date(meeting_date)}, {meeting_date.year}\n"
              "Made by speech recognition on this Mac, so expect some misheard words.\n"
              "'On Zoom' = Zoom call audio. 'In the room' = the room microphone.\n\n")
    body = main + ("\n\n=== After the call ===\n\n" + after if after else "")
    local_txt.write_text(header + body + "\n")

    # 4. Copy to the output folder
    if out_dir:
        folder, fell_back = Path(out_dir).expanduser(), False
        folder.mkdir(parents=True, exist_ok=True)
    else:
        folder, fell_back = output_folder(cfg)
    # The PDF is what people read. The .docx always stays in the session
    # folder (it is the source of the PDF and next week's "last week's
    # minutes"); it is copied out only if asked for, or if the PDF failed.
    want_pdf = cfg["output"].get("pdf", True)
    local_pdf = local_docx.with_suffix(".pdf")
    made_pdf = want_pdf and pdf.docx_to_pdf(local_docx, local_pdf)
    if want_pdf and not made_pdf:
        log("PDF could not be made, saving the .docx instead")
    save_docx = cfg["output"].get("save_docx", False) or not made_pdf

    prev = read_session(d)
    mine = {prev.get("output_pdf"), prev.get("output_docx")}
    exts = [".pdf"] * bool(made_pdf) + [".docx"] * bool(save_docx)
    base, n = stem, 2
    while any((folder / f"{base}{e}").exists() and str(folder / f"{base}{e}") not in mine for e in exts):
        base = f"{stem} ({n})"
        n += 1
    pdf_target = docx_target = txt_target = None
    if made_pdf:
        pdf_target = folder / f"{base}.pdf"
        shutil.copyfile(local_pdf, pdf_target)
        log(f"saved {pdf_target}")
    if save_docx:
        docx_target = folder / f"{base}.docx"
        shutil.copyfile(local_docx, docx_target)
        log(f"saved {docx_target}")
    if cfg["output"].get("save_transcript", True):
        txt_target = folder / f"{base} transcript.txt"
        shutil.copyfile(local_txt, txt_target)
    target = pdf_target or docx_target

    # 5. Clean up the raw audio
    if cfg["output"].get("delete_audio_after_success", True) and not read_session(d).get("external_audio"):
        shutil.rmtree(d / "audio", ignore_errors=True)
        for w in work.glob("*.wav"):
            w.unlink(missing_ok=True)
        log("deleted raw audio")

    update_session(d, status="done", output_docx=str(docx_target) if docx_target else None,
                   output_txt=str(txt_target) if txt_target else None,
                   output_pdf=str(pdf_target) if pdf_target else None,
                   meeting_date=meeting_date.isoformat())
    msg = f"Minutes saved: {target.name}"
    if fell_back:
        msg += " (output folder not found, saved to ~/MeetingMinutes/output)"
    notify(msg)
    return target


def pending(cfg):
    """Process sessions that stopped but were never finished, for example
    after a crash or a failed run. Skips a session that is still recording."""
    root = resolve(cfg["recording"]["sessions_dir"])
    if not root.exists():
        return
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        s = read_session(d)
        status = s.get("status")
        if status in ("done", "empty", None):
            continue
        rec_lock = d / "recording.lock"
        if rec_lock.exists() and pid_alive(rec_lock.read_text().strip()):
            continue
        if status == "failed" and int(s.get("attempts", 0)) >= 3:
            continue
        log(f"processing unfinished session {d.name}")
        try:
            run(cfg, d)
        except Exception:
            pass
