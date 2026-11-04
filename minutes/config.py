"""Loads config.toml and resolves paths."""

import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def config_path():
    return Path(os.environ.get("MINUTES_CONFIG") or ROOT / "config.toml")


def load(path=None):
    p = Path(path) if path else config_path()
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_root"] = str(ROOT)
    cfg["_path"] = str(p)
    return cfg


def resolve(path):
    """Expand ~ and make repo-relative paths absolute."""
    p = Path(os.path.expanduser(str(path)))
    return p if p.is_absolute() else ROOT / p


def attendee_groups(cfg):
    return [(g["label"], list(g.get("names", []))) for g in cfg.get("attendees", [])]
