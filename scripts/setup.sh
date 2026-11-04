#!/bin/bash
# One time setup. Everything here is free and runs on this Mac.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null; then
  echo "Install Homebrew first: https://brew.sh"; exit 1
fi

echo "1/6 Tools (ffmpeg, whisper.cpp, Ollama)"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp
brew list ollama >/dev/null 2>&1 || brew install ollama
brew services start ollama >/dev/null || true

echo "2/6 Python environment"
[ -d .venv ] || /usr/bin/python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "3/6 Speech model (about 1.6 GB, one time)"
mkdir -p models
if [ ! -f models/ggml-large-v3-turbo.bin ]; then
  curl -L --fail -o models/ggml-large-v3-turbo.bin.part \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
  mv models/ggml-large-v3-turbo.bin.part models/ggml-large-v3-turbo.bin
fi
if [ ! -f models/ggml-silero-v5.1.2.bin ]; then
  curl -L --fail -o models/ggml-silero-v5.1.2.bin \
    https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
fi

echo "4/6 Language model (about 5 GB, one time)"
MODEL=$(.venv/bin/python -c "from minutes.config import load; print(load()['llm']['model'])")
sleep 2
ollama pull "$MODEL"

echo "5/6 Menu bar app"
scripts/build_app.sh

echo "6/6 Schedule and start at login"
.venv/bin/python -m minutes install-launchd

echo
.venv/bin/python -m minutes doctor || true
echo
echo "Minutes are saved to ~/Downloads (change [output] folder in config.toml)."; echo "Next: click the menu bar icon, choose Start Recording once and allow"
echo "Microphone and Screen Recording when macOS asks."
