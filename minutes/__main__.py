"""Command line: python -m minutes <command>

  run SESSION_DIR [--date YYYY-MM-DD] [--force]   process a recorder session
  from-audio --room FILE [--zoom FILE] [--date YYYY-MM-DD] [--zoom-ended MM:SS] [--out DIR]
  pending                                          finish sessions left over after a crash
  config-json                                      settings for the menu bar app
  install-launchd / uninstall-launchd              schedule trigger and start at login
  doctor                                           check that everything is installed
"""

import argparse
import datetime as dt
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import pipeline
from .config import ROOT, load, resolve
from .dates import parse_date_arg, scheduled_start, weekday_index

APP_PATH = Path("~/Applications/MeetingMinutes.app").expanduser()
LABEL_APP = "com.meetingminutes.recorder"
LABEL_SCHEDULE = "com.meetingminutes.schedule"


def cmd_from_audio(cfg, a):
    """Run the pipeline on existing audio files (phase 1 testing, or a
    recording made some other way)."""
    files = {"mic": a.room, "system": a.zoom}
    if not any(files.values()):
        sys.exit("give --room and/or --zoom")
    if a.date:
        day = parse_date_arg(a.date)
    else:
        first = next(f for f in files.values() if f)
        day = dt.date.fromtimestamp(os.path.getmtime(first))
        print(f"[minutes] no --date given, using the file date {day.isoformat()}")
    t0 = scheduled_start(cfg, day).timestamp()
    root = resolve(cfg["recording"]["sessions_dir"])
    d = root / f"manual_{day.isoformat()}_{time.strftime('%H%M%S')}"
    d.mkdir(parents=True)
    with open(d / "chunks.jsonl", "w") as f:
        for track, path in files.items():
            if path:
                f.write(json.dumps({"track": track, "file": str(Path(path).resolve()), "start": t0}) + "\n")
    session = {"started_at": t0, "trigger": "file", "status": "stopped", "external_audio": True}
    if a.zoom_ended:
        parts = [int(x) for x in a.zoom_ended.split(":")]
        secs = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
        session["zoom_ended_at"] = t0 + secs
    pipeline.write_session(d, session)
    print(f"[minutes] session folder {d}")
    pipeline.run(cfg, d, date_override=day, out_dir=a.out)


def cmd_config_json(cfg):
    out = dict(cfg)
    out["_python"] = sys.executable
    out["_sessions_dir"] = str(resolve(cfg["recording"]["sessions_dir"]))
    out["_output_dir"] = str(resolve(cfg["output"]["folder"]))
    print(json.dumps(out))


def _agents_dir():
    d = Path("~/Library/LaunchAgents").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_install_launchd(cfg):
    exe = APP_PATH / "Contents/MacOS/MeetingMinutes"
    if not exe.exists():
        sys.exit(f"{exe} not found. Run scripts/build_app.sh first.")
    logs = Path("~/MeetingMinutes/logs").expanduser()
    logs.mkdir(parents=True, exist_ok=True)
    start = scheduled_start(cfg, dt.date.today()) - dt.timedelta(minutes=int(cfg["meeting"].get("pre_roll_minutes", 2)))
    # launchd counts weekdays from Sunday = 0. Python counts from Monday = 0.
    wd = (weekday_index(cfg["meeting"]["weekday"]) + 1) % 7
    if start.date() != dt.date.today():  # pre roll crossed midnight
        wd = (wd - 1) % 7
    agents = {
        LABEL_APP: {
            "Label": LABEL_APP,
            "ProgramArguments": [str(exe)],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Interactive",
            "StandardOutPath": str(logs / "recorder.log"),
            "StandardErrorPath": str(logs / "recorder.log"),
        },
        LABEL_SCHEDULE: {
            "Label": LABEL_SCHEDULE,
            "ProgramArguments": [str(exe), "--start", "--trigger", "schedule"],
            "StartCalendarInterval": {"Weekday": wd, "Hour": start.hour, "Minute": start.minute},
            "StandardOutPath": str(logs / "schedule.log"),
            "StandardErrorPath": str(logs / "schedule.log"),
        },
    }
    uid = os.getuid()
    auto = bool(cfg["recording"].get("auto_start", False))
    if not auto:
        # Manual mode: keep the menu bar app, remove the weekly trigger.
        path = _agents_dir() / f"{LABEL_SCHEDULE}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stderr=subprocess.DEVNULL)
        if path.exists():
            path.unlink()
            print(f"removed {path} (auto_start is off)")
        del agents[LABEL_SCHEDULE]
    for label, plist in agents.items():
        path = _agents_dir() / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stderr=subprocess.DEVNULL)
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(path)], check=False)
        print(f"installed {path}")
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    if auto:
        print(f"Recording will start every {days[wd]} at {start.strftime('%H:%M')}.")
    else:
        print("Manual mode: recording starts and stops only from the menu bar.")


def cmd_uninstall_launchd():
    uid = os.getuid()
    for label in (LABEL_APP, LABEL_SCHEDULE):
        path = _agents_dir() / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], stderr=subprocess.DEVNULL)
        path.unlink(missing_ok=True)
        print(f"removed {path}")


def cmd_doctor(cfg):
    ok = True

    def check(name, good, fix=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  [{'ok' if good else 'MISSING'}] {name}" + ("" if good else f"  -> {fix}"))

    print("Meeting Minutes setup check")
    check("ffmpeg", shutil.which("ffmpeg") or Path("/opt/homebrew/bin/ffmpeg").exists(), "brew install ffmpeg")
    check("whisper.cpp (whisper-cli)", shutil.which(cfg["transcription"]["whisper_bin"]) or
          Path("/opt/homebrew/bin/whisper-cli").exists(), "brew install whisper-cpp")
    check("whisper model", resolve(cfg["transcription"]["model"]).exists(), "run scripts/setup.sh")
    check("voice detection model (optional)", resolve(cfg["transcription"]["vad_model"]).exists(), "run scripts/setup.sh")
    if str(cfg["llm"].get("writer", "local")).lower() == "claude":
        from .claude_writer import _claude_bin
        try:
            check(f"Claude Code ({_claude_bin(cfg)})", True)
        except RuntimeError as e:
            check("Claude Code", False, str(e))
        ollama_ok = shutil.which("ollama") or Path("/opt/homebrew/bin/ollama").exists()
        check("Ollama installed (backup writer, started only when needed)", ollama_ok, "brew install ollama")
    else:
      try:
        with urllib.request.urlopen(cfg["llm"]["host"] + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read())["models"]]
        check("Ollama running", True)
        check(f"model {cfg['llm']['model']}", any(n.startswith(cfg["llm"]["model"]) for n in names),
              f"ollama pull {cfg['llm']['model']}")
      except OSError:
        check("Ollama running", False, "brew services start ollama")
    check("template", resolve(cfg["output"]["template"]).exists(), "set [output] template in config.toml")
    out = resolve(cfg["output"]["folder"])
    check(f"output folder {out}", out.exists() or out.parent.exists(), "set [output] folder in config.toml")
    check("menu bar app", APP_PATH.exists(), "scripts/build_app.sh")
    label = LABEL_SCHEDULE if cfg["recording"].get("auto_start", False) else LABEL_APP
    check("launchd agents", (_agents_dir() / f"{label}.plist").exists(), "python -m minutes install-launchd")
    check("PDF maker (Pages or LibreOffice)", Path("/Applications/Pages.app").exists() or shutil.which("soffice"),
          "install Pages from the App Store, or set [output] pdf = false")
    print("All good." if ok else "Some parts are missing.")
    return ok


def main():
    p = argparse.ArgumentParser(prog="minutes", description="Free, local meeting minutes.")
    p.add_argument("--config", help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("session")
    r.add_argument("--date", help="meeting date YYYY-MM-DD (default: when the recording started)")
    r.add_argument("--force", action="store_true", help="redo transcription and notes")
    r.add_argument("--out", help="save here instead of the configured folder")
    f = sub.add_parser("from-audio")
    f.add_argument("--room", help="room microphone audio (or a single mixed recording)")
    f.add_argument("--zoom", help="Zoom / system audio")
    f.add_argument("--date", help="meeting date YYYY-MM-DD")
    f.add_argument("--zoom-ended", help="time into the recording when Zoom ended, like 52:30")
    f.add_argument("--out", help="save here instead of the configured folder")
    sub.add_parser("pending")
    sub.add_parser("config-json")
    sub.add_parser("install-launchd")
    sub.add_parser("uninstall-launchd")
    sub.add_parser("doctor")
    a = p.parse_args()
    cfg = load(a.config)

    if a.cmd == "run":
        pipeline.run(cfg, a.session, parse_date_arg(a.date) if a.date else None, a.force, a.out)
    elif a.cmd == "from-audio":
        cmd_from_audio(cfg, a)
    elif a.cmd == "pending":
        pipeline.pending(cfg)
    elif a.cmd == "config-json":
        cmd_config_json(cfg)
    elif a.cmd == "install-launchd":
        cmd_install_launchd(cfg)
    elif a.cmd == "uninstall-launchd":
        cmd_uninstall_launchd()
    elif a.cmd == "doctor":
        sys.exit(0 if cmd_doctor(cfg) else 1)


if __name__ == "__main__":
    main()
