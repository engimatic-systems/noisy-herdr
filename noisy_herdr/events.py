"""Translate Herdr's seen/unseen status transitions into one sound per turn."""
import json
import os
import subprocess
import time

from . import audio
from .classify import inspect_event
from .storage import atomic_json, load_json, locked, session_key, settings, state_dir


def context_allows(current, foreground):
    """Native Herdr focus: the selected agent pane versus all other panes."""
    if foreground is True:
        return current["foreground"]
    if foreground is False:
        return current["background"]
    # Preserve the default behavior when no focus restriction is requested;
    # with a restriction, don't guess which context an unknown pane belongs to.
    return current["foreground"] and current["background"]


def advance(previous, status, observation=None):
    """Unknown preserves history; done -> idle means seen, not a second finish."""
    if status not in ("working", "blocked", "idle", "done"):
        return previous, None
    armed = previous.get("armed", False)
    alert = None
    current = dict(previous)
    if status == "working":
        armed = True
        current.pop("attention_key", None)
        current.pop("narration_notified", None)
    elif observation is not None:
        category = observation.category
        if category == "finished":
            completion = observation.identity
            if armed and (not completion or completion != previous.get("last_completion")):
                alert = "finished"
            armed = False
            if completion:
                current["last_completion"] = completion
            current.pop("attention_key", None)
        elif category in ("questions", "approvals"):
            key = category + ":" + observation.identity
            if key != previous.get("attention_key"):
                alert = category
            current["attention_key"] = key
        elif category == "narration":
            if (armed or status == "blocked") and not previous.get("narration_notified"):
                alert = "narration"
            current["narration_notified"] = True
            # A narrated pause must not consume the eventual completion alert.
    current.update(status=status, armed=armed, updated=time.time())
    return current, alert


def handle_event(event):
    data = event.get("data", {})
    kind = str(event.get("event", os.environ.get("HERDR_PLUGIN_EVENT", ""))).replace(".", "_")
    if not isinstance(data, dict):
        return None
    pane_id = data.get("pane_id")
    observation = None
    if (kind == "pane_agent_status_changed" and isinstance(pane_id, str)
            and data.get("agent_status") in ("blocked", "idle", "done")):
        observation = inspect_event(pane_id, data["agent_status"])
    root = state_dir()
    path = root / ("events-" + session_key() + ".json")
    alert = None
    with locked(root / ("events-" + session_key() + ".lock")):
        state = load_json(path, {})
        if kind == "pane_moved":
            old_id = data.get("previous_pane_id")
            pane = data.get("pane", {})
            new_id = pane.get("pane_id") if isinstance(pane, dict) else None
            if old_id in state and isinstance(new_id, str):
                state[new_id] = state.pop(old_id)
        elif kind in ("pane_closed", "pane_exited"):
            state.pop(pane_id, None)
        elif kind == "pane_agent_status_changed" and isinstance(pane_id, str):
            previous = state.get(pane_id, {})
            state[pane_id], alert = advance(previous, data.get("agent_status"), observation)
        else:
            return None
        cutoff = time.time() - 7 * 86400
        state = {key: value for key, value in state.items()
                 if isinstance(value, dict) and value.get("updated", 0) > cutoff}
        if len(state) > 1024:
            state = dict(sorted(state.items(), key=lambda item: item[1]["updated"])[-1024:])
        atomic_json(path, state)
    current = settings()
    if (alert and current["enabled"] and current[alert] and current["volume"]
            and context_allows(current, observation.foreground)):
        audio.play(current["sound"], current["volume"])
    return alert


def seed():
    """Remember working agents at startup/install without replaying old alerts."""
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    result = subprocess.run([binary, "agent", "list"], check=True, capture_output=True,
                            text=True, timeout=10)
    payload = json.loads(result.stdout)
    agents = payload.get("result", {}).get("agents", [])
    root = state_dir()
    key = session_key()
    with locked(root / ("events-" + key + ".lock")):
        state = {}
        for agent in agents:
            if isinstance(agent, dict) and agent.get("pane_id"):
                status = agent.get("agent_status")
                state[agent["pane_id"]] = {"status": status, "armed": status == "working",
                                            "updated": time.time()}
        atomic_json(root / ("events-" + key + ".json"), state)
