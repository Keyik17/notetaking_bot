"""Talks to the local Ollama server. Nothing leaves the Mac."""

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request


def log(msg):
    print(f"[llm] {msg}", flush=True)


def _req(cfg, path, body=None, timeout=1800):
    url = cfg["llm"]["host"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ensure_ready(cfg):
    """Start Ollama if needed and pull the model if missing."""
    try:
        _req(cfg, "/api/tags", timeout=5)
    except (urllib.error.URLError, OSError):
        exe = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
        log("starting ollama serve")
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        for _ in range(60):
            time.sleep(1)
            try:
                _req(cfg, "/api/tags", timeout=5)
                break
            except (urllib.error.URLError, OSError):
                pass
        else:
            raise RuntimeError("Ollama is not running and could not be started.")
    model = cfg["llm"]["model"]
    names = {m["name"] for m in _req(cfg, "/api/tags", timeout=10).get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        log(f"pulling {model} (one time download)")
        _req(cfg, "/api/pull", {"model": model, "stream": False}, timeout=7200)


def chat_json(cfg, system, user, schema, retries=2):
    """One chat call that must return JSON matching `schema`.

    The answer length is capped (`max_answer_tokens`) so a model stuck
    repeating itself stops instead of running until the timeout. Each retry
    raises the temperature a little to get it out of the loop."""
    body = {
        "model": cfg["llm"]["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": schema,
        "think": False,
        "keep_alive": "2m",
        "options": {
            "num_ctx": int(cfg["llm"].get("num_ctx", 32768)),
            "temperature": float(cfg["llm"].get("temperature", 0.2)),
            "num_predict": int(cfg["llm"].get("max_answer_tokens", 2048)),
        },
    }
    timeout = int(cfg["llm"].get("section_timeout_seconds", 600))
    last = None
    for attempt in range(retries + 1):
        if attempt:
            body["options"]["temperature"] = min(1.0, body["options"]["temperature"] + 0.2)
        try:
            t = time.time()
            r = _req(cfg, "/api/chat", body, timeout=timeout)
            log(f"  {r.get('prompt_eval_count', 0)} tokens in, {r.get('eval_count', 0)} out, "
                f"{time.time() - t:.0f}s")
            if r.get("done_reason") == "length":
                raise ValueError("answer hit the length cap (the model was probably repeating itself)")
            content = r["message"]["content"]
            return json.loads(content)
        except (ValueError, KeyError) as e:
            last = e
            log(f"bad answer from model, retrying ({attempt + 1}): {e}")
        except (socket.timeout, TimeoutError) as e:
            last = e
            log(f"model took longer than {timeout}s, retrying ({attempt + 1})")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if "think" in msg and "think" in body:
                body.pop("think")  # older Ollama or a model without thinking
                continue
            raise RuntimeError(f"Ollama error: {msg[:300]}")
    raise RuntimeError(f"model did not return a usable answer: {last}")
