"""Distinguish interactive prompts from prose using Herdr's live pane view.

Herdr 0.9.3 doesn't attach a blocker subtype to its events. Recognize actual
question/approval UI controls, not question marks or phrases in an assistant's
message. No terminal content is saved or logged.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
QUESTION_HEADER = re.compile(r"^\s*Question\s+\d+\s*/\s*\d+\b", re.I)
QUESTION_FOOTER = re.compile(r"\bto submit (?:answer|all)\b", re.I)
APPROVAL_FOOTER = re.compile(r"(?:press\s+)?\S+\s+to confirm\s+or\s+\S+\s+to cancel", re.I)
APPROVAL_HEADER = re.compile(
    r"(?:would you like to (?:run|grant|apply|allow)|allow (?:command|execution)|"
    r"do you (?:want to (?:proceed|run|allow)|trust)|trust this folder|"
    r"(?:grant|requesting|additional) permissions)", re.I)
CHOICE = re.compile(r"^\s*[›❯>]\s*\d+[.)]\s+", re.M)
WORKING_TIMER = re.compile(
    r"^\s*(?:[•◦]\s+)?[^\n›]*\(\s*(?:\d+[hm]\s+)*\d+s"
    r"(?:\s*[•·][^\n)]*)?\)(?:\s*·[^\n]*)?\s*$", re.M)
SETTLE_SECONDS = .4


@dataclass(frozen=True)
class Observation:
    category: str
    identity: str = ""
    foreground: Optional[bool] = None


def fingerprint(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def classify_view(text, info, event_status):
    focused = info.get("focused")
    foreground = focused if type(focused) is bool else None

    def observe(category, identity=""):
        return Observation(category, identity, foreground)

    text = ANSI.sub("", text).replace("\r", "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tail = lines[-8:]
    # A copied/quoted prompt above an active working footer isn't a live dialog.
    timer = WORKING_TIMER.search(text)
    question_footer = next((line for line in reversed(tail) if QUESTION_FOOTER.search(line)), None)
    approval_footer = next((line for line in reversed(tail) if APPROVAL_FOOTER.search(line)), None)
    footer = question_footer or approval_footer
    if timer and (footer is None or timer.start() > text.rfind(footer)):
        return observe("narration")
    if question_footer:
        headers = [i for i, line in enumerate(lines) if QUESTION_HEADER.search(line)]
        if headers:
            start = headers[-1]
            # Header + question identify the request. Moving a selection or
            # typing notes must not ring again for that same request.
            prompt = lines[start:start + 2]
            return observe("questions", fingerprint(prompt))
    if approval_footer and CHOICE.search(text):
        headers = [i for i, line in enumerate(lines) if APPROVAL_HEADER.search(line)]
        if headers:
            start = headers[-1]
            body = [line for line in lines[start:] if not re.match(r"^[›❯>]?\s*\d+[.)]", line)]
            return observe("approvals", fingerprint(body))
    live_status = info.get("agent_status", event_status)
    if live_status in ("working", "blocked", "unknown"):
        return observe("narration")
    if live_status in ("idle", "done"):
        # Herdr's completion marker is independent of whether the tab was seen.
        # A pause without that evidence is not a finished Codex turn.
        completion = info.get("completion_seq")
        if completion is not None and completion == info.get("state_change_seq"):
            return observe("finished", str(completion))
        if info.get("agent") == "codex":
            return observe("narration")
        # Other agents/older APIs may not expose sequence evidence.
        return observe("finished")
    return observe("narration")


def inspect_event(pane_id, status):
    """Read only the pane named by the event, with bounded subprocess timeouts."""
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    if status in ("idle", "done"):
        time.sleep(SETTLE_SECONDS)
    try:
        result = subprocess.run([binary, "agent", "get", pane_id], check=True,
                                capture_output=True, text=True, timeout=3)
        info = json.loads(result.stdout).get("result", {}).get("agent", {})
        if not isinstance(info, dict) or info.get("pane_id") != pane_id:
            return None
        screen = subprocess.run([binary, "agent", "read", pane_id, "--source", "detection",
                                 "--lines", "160"], check=True, capture_output=True,
                                text=True, timeout=3)
        return classify_view(screen.stdout, info, status)
    except (OSError, ValueError, subprocess.SubprocessError):
        # An unreadable/closed pane provides no trustworthy evidence to ring.
        return None
