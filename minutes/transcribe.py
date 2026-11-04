"""Audio to labelled transcript with whisper.cpp.

Each track ("system" = Zoom audio, "mic" = the room) is a list of chunk
files with wall-clock start times. Chunks that follow each other are joined
and sent to whisper.cpp together, then every segment gets an absolute time
so the two tracks can be merged and split at the moment Zoom closed.
"""

import json
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from .config import resolve

LABELS = {"system": "On Zoom", "mic": "In the room"}

HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "you",
    "subtitles by the amaraorg community", "bye", "okay", "so",
    "please subscribe", "music",
}


def log(msg):
    print(f"[transcribe] {msg}", flush=True)


def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def whisper_bin(cfg):
    b = cfg["transcription"].get("whisper_bin", "whisper-cli")
    found = shutil.which(b) or (b if Path(b).exists() else None)
    if not found:
        for cand in ("/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli"):
            if Path(cand).exists():
                return cand
        raise RuntimeError(f"whisper.cpp not found ({b}). Run scripts/setup.sh.")
    return found


def ffmpeg_bin():
    for cand in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("ffmpeg not found. Install it with: brew install ffmpeg")


def to_wav(src, dst):
    """Convert any audio file to 16 kHz mono WAV. Tolerates a chunk that was
    cut off by a crash. Returns the duration in seconds (0 if unreadable)."""
    r = _run([ffmpeg_bin(), "-nostdin", "-y", "-v", "error", "-err_detect", "ignore_err",
              "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    if not Path(dst).exists():
        log(f"could not read {src}: {r.stderr.strip()[:200]}")
        return 0.0
    return max(0.0, (Path(dst).stat().st_size - 44) / 32000.0)


def concat_wavs(paths, dst):
    if len(paths) == 1:
        shutil.copyfile(paths[0], dst)
        return
    lst = Path(dst).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in paths))
    r = _run([ffmpeg_bin(), "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(lst), "-c", "copy", str(dst)])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {r.stderr[:300]}")


def whisper(cfg, wav, out_base, prompt):
    t = cfg["transcription"]
    cmd = [whisper_bin(cfg), "-m", str(resolve(t["model"])), "-f", str(wav),
           "-l", t.get("language", "en"), "-t", str(t.get("threads", 8)),
           "-oj", "-of", str(out_base), "-np", "-ml", "80", "-sow"]
    if prompt:
        cmd += ["--prompt", prompt]
    vad = t.get("vad_model")
    if vad and resolve(vad).exists():
        cmd += ["--vad", "-vm", str(resolve(vad))]
    r = _run(cmd)
    out = Path(str(out_base) + ".json")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"whisper.cpp failed on {wav}: {r.stderr[-800:]}")
    data = json.loads(out.read_text(errors="replace"))
    segs = []
    for s in data.get("transcription", []):
        text = s.get("text", "").strip()
        if not text:
            continue
        a, b = s["offsets"]["from"] / 1000.0, s["offsets"]["to"] / 1000.0
        # After a silence, whisper can stretch a segment's start back to the
        # end of the previous one. Speech runs about 2.5 words a second.
        words = len(text.split())
        if b - a > words * 0.6 + 2:
            a = b - words * 0.4
        segs.append((a, b, text))
    return segs


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def transcribe_tracks(cfg, tracks, work_dir, prompt=""):
    """tracks: {"system": [(path, start_epoch), ...], "mic": [...]}.
    Returns a time-sorted list of segment dicts."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    segments = []
    for track, chunks in tracks.items():
        if not chunks:
            continue
        chunks = sorted(chunks, key=lambda c: c[1])
        # Convert, then group chunks that follow each other into runs.
        runs, cur, cur_end = [], [], None
        for i, (path, start) in enumerate(chunks):
            wav = work / f"{track}_{i:04d}.wav"
            dur = to_wav(path, wav)
            if dur < 0.5:
                continue
            if cur and cur_end is not None and start <= cur_end + 2.0:
                cur.append(wav)
                cur_end = max(cur_end, start + dur)
            else:
                if cur:
                    runs.append((cur, cur_start))
                cur, cur_start, cur_end = [wav], start, start + dur
        if cur:
            runs.append((cur, cur_start))
        for j, (wavs, start) in enumerate(runs):
            run_wav = work / f"{track}_run{j:02d}.wav"
            concat_wavs(wavs, run_wav)
            log(f"{LABELS.get(track, track)}: whisper on {run_wav.name}")
            for a, b, text in whisper(cfg, run_wav, work / f"{track}_run{j:02d}", prompt):
                segments.append({"track": track, "start": start + a, "end": start + b, "text": text})
            for w in wavs:
                w.unlink(missing_ok=True)
    segments.sort(key=lambda s: s["start"])
    return drop_echo(drop_noise(segments))


def drop_noise(segments):
    """Remove whisper's usual made-up lines and runs of the same line."""
    out = []
    for s in segments:
        n = _norm(s["text"])
        if not n or n in HALLUCINATIONS or re.fullmatch(r"[\[\(].*[\]\)]", s["text"].strip()):
            continue
        prev = [o for o in out[-3:] if o["track"] == s["track"]]
        if len(prev) >= 2 and all(_norm(p["text"]) == n for p in prev[-2:]):
            continue
        out.append(s)
    return out


def drop_echo(segments):
    """The room mic also hears the Zoom speakers. Drop room lines that
    repeat what the Zoom track said at the same moment."""
    zoom = [s for s in segments if s["track"] == "system"]
    out = []
    for s in segments:
        if s["track"] != "mic" or not zoom:
            out.append(s)
            continue
        near = " ".join(z["text"] for z in zoom
                        if z["end"] >= s["start"] - 3 and z["start"] <= s["end"] + 3)
        words = _norm(s["text"]).split()
        if near and words:
            zwords = set(_norm(near).split())
            overlap = sum(w in zwords for w in words) / len(words)
            ratio = SequenceMatcher(None, _norm(s["text"]), _norm(near)).ratio()
            if overlap >= 0.7 or ratio >= 0.6:
                continue
        out.append(s)
    return out


def fmt_offset(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def find_after_start(segments, zoom_ended_at):
    """When the Zoom call ended. Uses the recorder's mark if there is one,
    otherwise the last Zoom speech if the room kept talking for 2+ minutes."""
    if zoom_ended_at:
        return zoom_ended_at
    zoom = [s for s in segments if s["track"] == "system"]
    if not zoom:
        return None
    last = max(z["end"] for z in zoom)
    after = [s for s in segments if s["track"] == "mic" and s["start"] > last + 20]
    if after and after[-1]["end"] - after[0]["start"] >= 120:
        return last + 20
    return None


def to_text(segments, t0):
    """Readable lines. Consecutive lines from the same track are joined."""
    lines, cur = [], None
    for s in segments:
        if cur and cur["track"] == s["track"] and s["start"] - cur["end"] < 1.5 and len(cur["text"]) < 600:
            cur["text"] += " " + s["text"]
            cur["end"] = s["end"]
            continue
        if cur:
            lines.append(cur)
        cur = dict(s)
    if cur:
        lines.append(cur)
    return "\n".join(f"[{fmt_offset(l['start'] - t0)}] {LABELS.get(l['track'], l['track'])}: {l['text']}"
                     for l in lines)


def split(segments, t0, after_start):
    if after_start is None:
        return to_text(segments, t0), ""
    main = [s for s in segments if s["start"] < after_start]
    after = [s for s in segments if s["start"] >= after_start]
    return to_text(main, t0), to_text(after, t0)
