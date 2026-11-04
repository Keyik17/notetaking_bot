"""Makes a PDF copy of the minutes. Uses LibreOffice if it is installed,
otherwise Pages (free, comes with macOS). Both run on this Mac.

The first time Pages is used, macOS asks once whether MeetingMinutes may
control Pages (Privacy & Security, Automation)."""

import shutil
import subprocess
import time
from pathlib import Path


def log(msg):
    print(f"[pdf] {msg}", flush=True)


PAGES_SCRIPT = """
on run argv
    set inFile to POSIX file (item 1 of argv)
    set outFile to POSIX file (item 2 of argv)
    set wasRunning to application "Pages" is running
    tell application "Pages"
        launch
        -- Pages answers "Connection is invalid" while it is still starting.
        repeat 60 times
            try
                get name of every document
                exit repeat
            on error
                delay 1
            end try
        end repeat
        set d to open inFile
        repeat 30 times
            try
                get name of d
                exit repeat
            on error
                delay 1
            end try
        end repeat
        delay 2
        export d to outFile as PDF
        close d saving no
        if not wasRunning then quit
    end tell
end run
"""


def _soffice():
    for p in (shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if p and Path(p).exists():
            return p
    return None


def docx_to_pdf(docx: Path, pdf: Path, attempts=3) -> bool:
    """Returns True if `pdf` was written. Never raises: the .docx is the
    main result, the PDF is a convenience copy."""
    docx, pdf = Path(docx), Path(pdf)
    pdf.unlink(missing_ok=True)
    soffice = _soffice()
    if soffice:
        r = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx)],
                           capture_output=True, text=True, timeout=300)
        made = pdf.parent / (docx.stem + ".pdf")
        if made.exists() and made != pdf:
            made.replace(pdf)
        if pdf.exists():
            return True
        log(f"LibreOffice failed, trying Pages: {r.stderr.strip()[:200]}")
    if not Path("/Applications/Pages.app").exists():
        log("no PDF: neither LibreOffice nor Pages is installed")
        return False
    for i in range(attempts):
        r = subprocess.run(["osascript", "-", str(docx), str(pdf)], input=PAGES_SCRIPT,
                           capture_output=True, text=True, timeout=300)
        if pdf.exists() and pdf.stat().st_size > 0:
            return True
        log(f"Pages export failed (try {i + 1}): {r.stderr.strip()[:200]}")
        time.sleep(5)
    return False
