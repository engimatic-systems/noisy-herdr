import argparse
import json
import os
import subprocess
import sys

from . import PLUGIN_ID, VERSION, audio, events
from .storage import SOUND_IDS, settings, toggle, update_settings


def open_panel(focus=True):
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    command = [binary, "plugin", "pane", "open", "--plugin", PLUGIN_ID,
               "--entrypoint", "settings"]
    if os.environ.get("HERDR_WORKSPACE_ID"):
        command += ["--workspace", os.environ["HERDR_WORKSPACE_ID"]]
    command += ["--focus" if focus else "--no-focus"]
    result = subprocess.run(command, timeout=10, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
    else:
        # Herdr labels the pane from the manifest, but gives new tabs an
        # automatic cwd-derived label. Name only the tab we just created.
        payload = json.loads(result.stdout)
        pane = payload.get("result", {}).get("plugin_pane", {}).get("pane", {})
        tab_id = pane.get("tab_id")
        if tab_id:
            renamed = subprocess.run([binary, "tab", "rename", tab_id, "Noisy Herdr"],
                                     capture_output=True, text=True, timeout=10)
            if renamed.returncode:
                print("Settings opened; tab rename failed: " + renamed.stderr.strip(), file=sys.stderr)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Noisy Herdr: focus-independent agent sounds")
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("event", "seed", "settings", "open", "toggle", "enable", "disable", "status"):
        commands.add_parser(name)
    preview = commands.add_parser("preview", help="Play a sound, even when alerts are off")
    preview.add_argument("--sound", choices=sorted(SOUND_IDS))
    preview.add_argument("--volume", type=int, choices=range(101), metavar="0..100")
    install = commands.add_parser("install", help="Link plugin and disable duplicate built-in sounds")
    install.add_argument("--keep-built-in", action="store_true", dest="keep_builtin")
    args = parser.parse_args(argv)
    try:
        if args.command == "event":
            raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Expected a Herdr event object")
            events.handle_event(payload)
        elif args.command == "seed":
            events.seed()
        elif args.command == "settings":
            from .ui import run
            run()
        elif args.command == "open":
            return open_panel()
        elif args.command == "preview":
            current = settings()
            audio.play(args.sound or current["sound"],
                       current["volume"] if args.volume is None else args.volume)
        elif args.command == "toggle":
            print("Noisy Herdr " + ("on" if toggle()["enabled"] else "off"))
        elif args.command in ("enable", "disable"):
            update_settings(enabled=args.command == "enable")
            print("Noisy Herdr " + ("on" if args.command == "enable" else "off"))
        elif args.command == "status":
            print(json.dumps(settings(), indent=2))
        elif args.command == "install":
            from .install import install
            return install(args.keep_builtin)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        message = str(error)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail = error.stderr
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            message += ": " + detail.strip()[:500]
        print("Noisy Herdr: " + message, file=sys.stderr)
        return 1
    return 0
