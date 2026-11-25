"""A small mouse-enabled terminal settings panel, hosted by a Herdr tab."""
import curses
import os
import signal
import subprocess
import sys
from pathlib import Path

from .storage import SOUNDS, settings, toggle, update_settings


class Preview:
    def __init__(self):
        self.process = None

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.process.communicate(timeout=2)
            self.process = None

    def play(self, current):
        self.stop()
        entry = Path(__file__).resolve().parent.parent / "noisy-herdr.py"
        self.process = subprocess.Popen(
            [sys.executable, str(entry), "preview", "--sound", current["sound"],
             "--volume", str(current["volume"])], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)

    def error(self):
        if self.process is not None and self.process.poll() is not None:
            process = self.process
            self.process = None
            _, error = process.communicate()
            if process.returncode:
                return error.decode(errors="replace").strip().split("\n")[-1]
        return None


class Panel:
    def __init__(self, screen):
        self.screen = screen
        self.current = settings()
        self.focus = "enabled"
        self.message = "Your changes save automatically."
        self.hits = []
        self.preview = Preview()
        self.colors = False
        self.slider = (0, 0, 1)

    @property
    def controls(self):
        return ["enabled", "minus", "slider", "plus", *[s[0] for s in SOUNDS],
                "preview", "finished", "questions", "approvals", "narration", "close"]

    def save(self, **changes):
        self.current = update_settings(**changes)
        self.message = "Saved."

    def activate(self, control, x=None):
        self.focus = control
        try:
            if control == "close":
                return False
            if control == "enabled":
                self.current = toggle()
                self.message = "Sound alerts " + ("on." if self.current["enabled"] else "off.")
            elif control in ("minus", "plus"):
                delta = 5 if control == "plus" else -5
                self.save(volume=max(0, min(100, self.current["volume"] + delta)))
            elif control == "slider" and x is not None:
                _, start, length = self.slider
                self.save(volume=max(0, min(100, round(100 * (x - start) / (length - 1)))))
            elif control in ("finished", "narration", "questions", "approvals"):
                self.save(**{control: not self.current[control]})
            elif control in {sound[0] for sound in SOUNDS}:
                self.save(sound=control)
                self.preview.play(self.current)
                self.message = "Saved. Playing " + next(s[1] for s in SOUNDS if s[0] == control) + "."
            elif control == "preview":
                self.preview.play(self.current)
                self.message = "Playing preview at " + str(self.current["volume"]) + "%."
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.message = str(error)
        return True

    def put(self, y, x, text, style=0):
        height, width = self.screen.getmaxyx()
        if 0 <= y < height and 0 <= x < width - 1:
            try:
                self.screen.addnstr(y, x, text, width - x - 1, style)
            except curses.error:
                pass

    def button(self, y, x, label, control, style=0, width=None):
        label = label.ljust(width or len(label))
        self.hits.append((y, x, x + len(label), control))
        if self.focus == control:
            style |= curses.A_REVERSE
        self.put(y, x, label, style)

    def draw(self):
        screen = self.screen
        screen.erase()
        self.hits = []
        height, width = screen.getmaxyx()
        accent = curses.color_pair(1) if self.colors else curses.A_BOLD
        dim = curses.A_DIM
        if height < 25 or width < 58:
            self.put(1, 2, "Noisy Herdr", accent | curses.A_BOLD)
            self.put(3, 2, "Enlarge this panel to at least 58 x 25 cells.")
            self.put(5, 2, "Esc / Q to close")
            screen.refresh()
            return
        right = min(width - 3, 73)
        self.put(1, 3, "(o)  NOISY HERDR", accent | curses.A_BOLD)
        self.button(1, right - 9, "[ Close ]", "close", dim)
        self.put(2, 3, "A little bing when your agents are ready.", dim)
        enabled = self.current["enabled"]
        self.button(4, 3, "Sound alerts    [ " + ("ON " if enabled else "OFF") + " ]", "enabled",
                    accent | curses.A_BOLD)
        self.put(5, 3, "Plays in foreground and background.", dim)
        self.put(7, 3, "VOLUME", curses.A_BOLD)
        self.button(7, 12, "[-]", "minus")
        length = max(12, right - 34)
        start = 17
        self.slider = (7, start, length)
        position = round((length - 1) * self.current["volume"] / 100)
        track = "=" * position + "O" + "-" * (length - position - 1)
        self.button(7, start, track, "slider", accent)
        self.button(7, start + length + 2, "[+]", "plus")
        self.put(7, start + length + 7, str(self.current["volume"]).rjust(3) + "%")
        self.put(9, 3, "SOUND", curses.A_BOLD)
        self.put(9, 12, "Click to choose and listen", dim)
        for index, (sound, title, description) in enumerate(SOUNDS):
            marker = "(*) " if self.current["sound"] == sound else "( ) "
            label = marker + title.ljust(17) + " " + description
            self.button(11 + index, 3, label[:right - 3], sound,
                        accent if self.current["sound"] == sound else 0)
        self.button(17, 3, "[ > Preview sound ]", "preview", accent)
        self.button(19, 3, ("[x]" if self.current["finished"] else "[ ]") + " Finished tasks", "finished")
        self.button(19, 28, ("[x]" if self.current["questions"] else "[ ]") + " Agent questions", "questions")
        self.button(20, 3, ("[x]" if self.current["approvals"] else "[ ]") + " Approval requests", "approvals")
        self.button(20, 28, ("[x]" if self.current["narration"] else "[ ]") + " Narration / updates", "narration")
        self.put(22, 3, self.message, dim)
        self.put(24, 3, "Tab / arrows to move   Space to select   - / + volume   Q close", dim)
        screen.refresh()

    def mouse(self):
        try:
            _, x, y, _, flags = curses.getmouse()
        except curses.error:
            return True
        if flags & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
            for row, start, end, control in self.hits:
                if y == row and start <= x < end:
                    return self.activate(control, x)
        return True

    def run(self):
        screen = self.screen
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, 209 if curses.COLORS >= 256 else curses.COLOR_RED, -1)
            self.colors = True
        curses.mouseinterval(0)
        curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED | curses.BUTTON1_CLICKED)
        screen.keypad(True)
        screen.timeout(200)
        try:
            while True:
                try:
                    self.current = settings()
                except (OSError, ValueError) as error:
                    self.message = str(error)
                error = self.preview.error()
                if error:
                    self.message = error
                self.draw()
                key = screen.getch()
                if key in (27, ord("q"), ord("Q")):
                    break
                if key == curses.KEY_MOUSE:
                    if not self.mouse():
                        break
                elif key in (9, curses.KEY_DOWN, curses.KEY_UP, curses.KEY_BTAB):
                    controls = self.controls
                    delta = -1 if key in (curses.KEY_UP, curses.KEY_BTAB) else 1
                    self.focus = controls[(controls.index(self.focus) + delta) % len(controls)]
                elif key in (10, 13, ord(" ")):
                    if not self.activate(self.focus):
                        break
                elif key in (ord("-"), curses.KEY_LEFT):
                    self.activate("minus")
                elif key in (ord("+"), ord("="), curses.KEY_RIGHT):
                    self.activate("plus")
                elif ord("1") <= key <= ord("5"):
                    self.activate(SOUNDS[key - ord("1")][0])
                elif key in (ord("p"), ord("P")):
                    self.activate("preview")
        finally:
            self.preview.stop()
            curses.mousemask(0)


def run():
    curses.wrapper(lambda screen: Panel(screen).run())
