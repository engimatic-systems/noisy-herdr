#!/usr/bin/env python3
"""Validate manifest and installer using the installed Herdr in isolated dirs."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    with tempfile.TemporaryDirectory(prefix="noisy-herdr-check-") as folder:
        base = Path(folder)
        config = base / "config" / "herdr" / "config.toml"
        config.parent.mkdir(parents=True)
        original = 'onboarding = false\n\n[ui.toast]\ndelivery = "herdr"\n\n[theme]\nname = "gruvbox"\n'
        config.write_text(original)
        env = dict(os.environ, XDG_CONFIG_HOME=str(base / "config"),
                   XDG_STATE_HOME=str(base / "state"), HERDR_CONFIG_PATH=str(config),
                   HERDR_SOCKET_PATH=str(base / "absent.sock"),
                   HERDR_PLUGIN_CONFIG_DIR=str(base / "plugin-config"),
                   HERDR_PLUGIN_STATE_DIR=str(base / "plugin-state"))
        for name in ("HERDR_SESSION", "HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID"):
            env.pop(name, None)
        linked = subprocess.run([binary, "plugin", "link", str(ROOT)], env=env,
                                capture_output=True, text=True, check=True, timeout=15)
        plugin = json.loads(linked.stdout)["result"]["plugin"]
        assert plugin["plugin_id"] == "noisy-herdr"
        assert plugin.get("warnings", []) == [], plugin.get("warnings")
        assert plugin["panes"][0]["placement"] == "tab"
        assert {event["on"] for event in plugin["events"]} >= {"pane.agent_status_changed"}
        for _ in range(2):
            result = subprocess.run([sys.executable, str(ROOT / "noisy-herdr.py"), "install"],
                                    env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
        assert config.read_text().startswith(original)
        assert "[ui.sound]\nenabled = false" in config.read_text()
        backups = list(config.parent.glob("*.before-noisy-herdr-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == original
        preferences = json.loads((base / "plugin-config" / "settings.json").read_text())
        assert preferences["sound"] == "pomodoro" and preferences["volume"] == 60
        print("Herdr manifest accepted with no warnings. Isolated installation passed:")
        print("config validation, backup, duplicate-sound prevention, preferences, repeat install.")


if __name__ == "__main__":
    main()
