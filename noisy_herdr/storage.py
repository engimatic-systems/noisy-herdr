"""Atomic settings and session-scoped event state, shared by short-lived hooks."""
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import PLUGIN_ID

SOUNDS = (
    ("pomodoro", "Pomodoro bing", "A bright, ringing kitchen timer"),
    ("glass", "Glass ping", "A clear little tap on crystal"),
    ("chime", "Soft chime", "Two gentle, ascending notes"),
    ("wood", "Woodblock", "A short, mellow wooden knock"),
    ("arcade", "Arcade", "A cheerful, three-note power-up"),
)
SOUND_IDS = {item[0] for item in SOUNDS}
DEFAULTS = {"enabled": True, "volume": 60, "sound": "pomodoro",
            "foreground": True, "background": True,
            "finished": True, "narration": False, "questions": True, "approvals": True}
BOOLEAN_SETTINGS = ("enabled", "foreground", "background", "finished", "narration", "questions", "approvals")


def herdr_config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "herdr"


def config_dir():
    return Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR",
                               herdr_config_dir() / "plugins" / "config" / PLUGIN_ID))


def state_dir():
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", base / "herdr" / "plugins" / PLUGIN_ID))


def session_key():
    socket = os.environ.get("HERDR_SOCKET_PATH", str(herdr_config_dir() / "herdr.sock"))
    return hashlib.sha256(socket.encode()).hexdigest()[:20]


def load_json(path, default):
    try:
        with path.open(encoding="utf-8") as stream:
            result = json.load(stream)
    except FileNotFoundError:
        return default.copy()
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def settings():
    path = config_dir() / "settings.json"
    saved = load_json(path, {})
    # Preserve the old Needs input choice, while making narration opt-in.
    legacy = saved.pop("attention", True)
    if type(legacy) is not bool:
        raise ValueError(f"attention must be true or false in {path}")
    result = {**DEFAULTS, "questions": legacy, "approvals": legacy, **saved}
    for key in BOOLEAN_SETTINGS:
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be true or false in {path}")
    if type(result["volume"]) is not int or not 0 <= result["volume"] <= 100:
        raise ValueError(f"volume must be an integer from 0 to 100 in {path}")
    if not isinstance(result["sound"], str) or result["sound"] not in SOUND_IDS:
        raise ValueError(f"Unknown sound in {path}")
    return result


def update_settings(**changes):
    with locked(config_dir() / "settings.lock"):
        current = settings()
        current.update(changes)
        # Round-trip validation before replacing the user's file.
        for key in BOOLEAN_SETTINGS:
            if type(current[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        if type(current["volume"]) is not int or not 0 <= current["volume"] <= 100:
            raise ValueError("volume must be an integer from 0 to 100")
        if not isinstance(current["sound"], str) or current["sound"] not in SOUND_IDS:
            raise ValueError("Unknown sound")
        atomic_json(config_dir() / "settings.json", current)
        return current


def toggle():
    with locked(config_dir() / "settings.lock"):
        current = settings()
        current["enabled"] = not current["enabled"]
        atomic_json(config_dir() / "settings.json", current)
        return current
