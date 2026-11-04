#!/bin/bash
# Builds the self-contained Meeting Minutes app.
#   dist/MeetingMinutes.app   the app
#   dist/MeetingMinutes.zip   the file to give teammates
# and installs a copy to ~/Applications.
#
# The team's free Gemini API key is read from gemini_api_key.txt (not in the
# repo) and built into the app, so teammates never need an account.
set -euo pipefail
cd "$(dirname "$0")/.."
DIST="dist"
APP="$DIST/MeetingMinutes.app"

echo "Building..."
(cd recorder && swift build -c release)
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp recorder/.build/release/MeetingMinutes "$APP/Contents/MacOS/MeetingMinutes"
cp recorder/Resources/last_minutes_seed.json "$APP/Contents/Resources/"

KEY=""
[ -f gemini_api_key.txt ] && KEY="$(tr -d '[:space:]' < gemini_api_key.txt)"
/usr/bin/python3 - "$KEY" <<'PY'
import json, sys
s = json.load(open("recorder/Resources/default_settings.json"))
s["gemini"]["api_key"] = sys.argv[1]
json.dump(s, open("dist/MeetingMinutes.app/Contents/Resources/default_settings.json", "w"), indent=2)
PY
[ -z "$KEY" ] && echo "WARNING: no gemini_api_key.txt, so the app cannot write minutes."

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.meetingminutes.recorder</string>
  <key>CFBundleName</key><string>MeetingMinutes</string>
  <key>CFBundleDisplayName</key><string>Meeting Minutes</string>
  <key>CFBundleExecutable</key><string>MeetingMinutes</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppleEventsUsageDescription</key>
  <string>Checks your browser tabs for a Zoom meeting, to know when the call ends. Nothing else is read.</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Records the people in the room during the meeting.</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Records the Zoom call audio (no video).</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Turns the meeting recording into a transcript on this Mac.</string>
</dict>
</plist>
PLIST

# Free ad hoc signature (no paid Apple developer account).
codesign --force --deep --sign - "$APP"
rm -f "$DIST/MeetingMinutes.zip"
ditto -c -k --keepParent "$APP" "$DIST/MeetingMinutes.zip"

# Install for this Mac.
pkill -x MeetingMinutes 2>/dev/null || true
rm -rf "$HOME/Applications/MeetingMinutes.app"
mkdir -p "$HOME/Applications"
cp -R "$APP" "$HOME/Applications/"
echo "Built $APP and $DIST/MeetingMinutes.zip, installed to ~/Applications"
