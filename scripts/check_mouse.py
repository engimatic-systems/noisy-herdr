#!/usr/bin/env python3
"""Exercise the real curses app through an xterm mouse protocol PTY.

Uses isolated settings and volume zero when testing previews. No real audio.
"""
import fcntl
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix="noisy-herdr-mouse-") as folder:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 27, 76, 0, 0))
        env = dict(os.environ, TERM="xterm-256color", HERDR_PLUGIN_CONFIG_DIR=folder,
                   HERDR_PLUGIN_STATE_DIR=folder)
        process = subprocess.Popen([sys.executable, str(ROOT / "noisy-herdr.py"), "settings"],
                                   stdin=slave, stdout=slave, stderr=slave, env=env,
                                   start_new_session=True)
        os.close(slave)
        transcript = bytearray()

        def drain(seconds=.15):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                if select.select([master], [], [], .02)[0]:
                    try:
                        transcript.extend(os.read(master, 65536))
                    except OSError:
                        return

        def await_value(key, expected):
            for _ in range(30):
                drain(.03)
                try:
                    current = json.loads((Path(folder) / "settings.json").read_text())
                except (FileNotFoundError, ValueError):
                    continue
                if current[key] == expected:
                    return
            raise AssertionError(f"Mouse change did not persist {key}={expected!r}; {transcript[-1800:]!r}")

        def click(x, y):
            # Apple's system ncurses requests legacy X10 mouse reports;
            # newer ncurses/terminfo opts into SGR. Emulate the protocol
            # the application actually requested, as Herdr does.
            if b"\x1b[?1006h" in transcript:
                packet = f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode()
            else:
                packet = b"\x1b[M" + bytes((32, x + 33, y + 33))
                packet += b"\x1b[M" + bytes((35, x + 33, y + 33))
            os.write(master, packet)

        try:
            for _ in range(30):
                drain(.03)
                if b"NOISY HERDR" in transcript:
                    break
            assert b"NOISY HERDR" in transcript, transcript.decode(errors="replace")
            click(20, 4)
            await_value("enabled", False)
            click(7, 5)
            await_value("foreground", False)
            click(31, 5)
            await_value("background", False)
            click(7, 5)
            await_value("foreground", True)
            click(31, 5)
            await_value("background", True)
            click(55, 7)
            await_value("volume", 100)
            click(17, 7)
            await_value("volume", 0)
            click(10, 12)
            await_value("sound", "glass")
            click(5, 19)
            await_value("finished", False)
            click(30, 19)
            await_value("questions", False)
            click(8, 20)
            await_value("approvals", False)
            click(31, 20)
            await_value("narration", True)
            # Rendering narrower windows must remain usable and resize back.
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 44, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
            drain(.25)
            assert b"Enlarge this panel" in transcript
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 27, 76, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
            drain(.25)
            click(67, 1)
            for _ in range(50):
                drain(.1)
                if process.poll() is not None:
                    break
            if process.poll() is None:
                raise AssertionError(f"Close did not exit after resize: {transcript[-3500:]!r}")
            assert process.returncode == 0, transcript.decode(errors="replace")
            print("Real terminal mouse checks passed: toggle, volume endpoints, sound selection,")
            print("alert filters, resize recovery, and Close. No audio was played.")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                drain(.2)
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            os.close(master)


if __name__ == "__main__":
    main()
