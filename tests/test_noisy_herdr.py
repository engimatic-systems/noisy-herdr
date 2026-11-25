import array
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from noisy_herdr import audio, events, storage
from noisy_herdr.install import mute_builtin
from noisy_herdr.ui import Panel
from noisy_herdr.cli import main, open_panel
from noisy_herdr.classify import Observation


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "HERDR_PLUGIN_CONFIG_DIR": str(base / "config"),
            "HERDR_PLUGIN_STATE_DIR": str(base / "state"),
            "HERDR_SOCKET_PATH": str(base / "herdr.sock"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def event(self, status, pane="w1:p1"):
        return {"event": "pane_agent_status_changed", "data": {
            "type": "pane_agent_status_changed", "pane_id": pane,
            "workspace_id": "w1", "agent_status": status, "agent": "codex"}}


class EventTests(Isolated):
    def setUp(self):
        super().setUp()
        # Default fixture is a verified question or completion. Tests for
        # narration, unavailable evidence, and approvals override this.
        inspect = patch("noisy_herdr.events.inspect_event", side_effect=lambda pane, status:
                        Observation("questions" if status == "blocked" else "finished"))
        self.inspect = inspect.start()
        self.addCleanup(inspect.stop)

    @patch("noisy_herdr.audio.play")
    def test_foreground_idle_and_background_done_each_ring_once(self, play):
        for final, pane in (("idle", "w1:p1"), ("done", "w1:p2")):
            for status in ("working", "working", "unknown", final, final, "idle"):
                events.handle_event(self.event(status, pane))
        self.assertEqual(play.call_count, 2)
        play.assert_called_with("pomodoro", 60)

    @patch("noisy_herdr.audio.play")
    def test_startup_does_not_ring_and_questions_rearm(self, play):
        for status in ("idle", "done", "idle"):
            events.handle_event(self.event(status))
        play.assert_not_called()
        for status in ("working", "blocked", "unknown", "blocked", "working", "blocked"):
            events.handle_event(self.event(status))
        self.assertEqual(play.call_count, 2)

    @patch("noisy_herdr.audio.play")
    def test_mute_and_reenable_do_not_replay_a_completion(self, play):
        storage.update_settings(enabled=False)
        for status in ("working", "idle"):
            events.handle_event(self.event(status))
        storage.update_settings(enabled=True)
        events.handle_event(self.event("idle"))
        play.assert_not_called()
        events.handle_event(self.event("working"))
        events.handle_event(self.event("idle"))
        play.assert_called_once()

    @patch("noisy_herdr.audio.play")
    def test_volume_zero_and_event_filters(self, play):
        storage.update_settings(volume=0)
        events.handle_event(self.event("working"))
        events.handle_event(self.event("idle"))
        storage.update_settings(volume=31, sound="wood", questions=False)
        events.handle_event(self.event("working"))
        events.handle_event(self.event("blocked"))
        play.assert_not_called()
        events.handle_event(self.event("idle"))
        play.assert_called_once_with("wood", 31)

    @patch("noisy_herdr.audio.play")
    def test_narration_is_quiet_and_does_not_consume_real_completion(self, play):
        events.handle_event(self.event("working"))
        self.inspect.return_value = Observation("narration")
        self.inspect.side_effect = None
        events.handle_event(self.event("idle"))
        events.handle_event(self.event("blocked"))
        play.assert_not_called()
        self.inspect.return_value = Observation("finished", "turn-2")
        events.handle_event(self.event("done"))
        play.assert_called_once_with("pomodoro", 60)

    @patch("noisy_herdr.audio.play")
    def test_three_attention_categories_are_independent(self, play):
        storage.update_settings(narration=True, questions=False, approvals=True)
        self.inspect.side_effect = None
        events.handle_event(self.event("working"))
        self.inspect.return_value = Observation("narration")
        events.handle_event(self.event("blocked"))
        self.assertEqual(play.call_count, 1)
        events.handle_event(self.event("working"))
        self.inspect.return_value = Observation("questions", "q1")
        events.handle_event(self.event("blocked"))
        self.assertEqual(play.call_count, 1)
        self.inspect.return_value = Observation("approvals", "a1")
        events.handle_event(self.event("blocked"))
        self.assertEqual(play.call_count, 2)
        events.handle_event(self.event("blocked"))
        self.assertEqual(play.call_count, 2)

    @patch("noisy_herdr.audio.play")
    def test_new_question_in_same_blocked_state_is_detected(self, play):
        self.inspect.side_effect = None
        self.inspect.return_value = Observation("questions", "first-question")
        events.handle_event(self.event("blocked"))
        self.inspect.return_value = Observation("questions", "second-question")
        events.handle_event(self.event("blocked"))
        self.assertEqual(play.call_count, 2)

    @patch("noisy_herdr.audio.play")
    def test_unreadable_pane_is_quiet_and_can_still_complete_later(self, play):
        self.inspect.side_effect = None
        events.handle_event(self.event("working"))
        self.inspect.return_value = None
        events.handle_event(self.event("idle"))
        play.assert_not_called()
        self.inspect.return_value = Observation("finished", "123")
        events.handle_event(self.event("idle"))
        play.assert_called_once()

    @patch("noisy_herdr.audio.play")
    def test_late_working_event_does_not_repeat_same_completion(self, play):
        self.inspect.side_effect = None
        self.inspect.return_value = Observation("finished", "123")
        for status in ("working", "done", "working", "done", "idle"):
            events.handle_event(self.event(status))
        play.assert_called_once()

    @patch("noisy_herdr.audio.play")
    def test_closed_panes_and_moved_panes(self, play):
        events.handle_event(self.event("working"))
        events.handle_event({"event": "pane_moved", "data": {
            "previous_pane_id": "w1:p1", "pane": {"pane_id": "w2:p8"}}})
        events.handle_event(self.event("idle", "w2:p8"))
        play.assert_called_once()
        events.handle_event(self.event("working", "w2:p8"))
        events.handle_event({"event": "pane_closed", "data": {"pane_id": "w2:p8"}})
        events.handle_event(self.event("idle", "w2:p8"))
        self.assertEqual(play.call_count, 1)

    @patch("noisy_herdr.audio.play")
    def test_same_pane_ids_on_different_sessions_are_independent(self, play):
        events.handle_event(self.event("working"))
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/different/session.sock"}):
            events.handle_event(self.event("idle"))
        play.assert_not_called()
        events.handle_event(self.event("idle"))
        play.assert_called_once()

    @patch("noisy_herdr.audio.play")
    def test_concurrent_duplicate_events_ring_once(self, play):
        events.handle_event(self.event("working"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(events.handle_event, [self.event("idle")] * 16))
        play.assert_called_once()

    @patch("noisy_herdr.audio.play")
    def test_concurrent_processes_use_the_same_lock(self, play):
        # Volume zero keeps the actual worker processes silent. The persisted
        # armed bit must be consumed without truncation or lost writes.
        storage.update_settings(volume=0)
        events.handle_event(self.event("working"))
        entry = Path(__file__).resolve().parents[1] / "noisy-herdr.py"
        fake_herdr = Path(self.temp.name) / "herdr-fixture"
        fake_herdr.write_text(
            "#!" + sys.executable + "\nimport json,sys\n"
            "if sys.argv[1:3] == ['agent', 'get']:\n"
            "    print(json.dumps({'result': {'agent': {'pane_id':'w1:p1',"
            "'agent':'codex','agent_status':'idle','completion_seq':10,'state_change_seq':10}}}))\n"
            "else:\n    print('The task is complete.')\n")
        fake_herdr.chmod(0o755)
        env = dict(os.environ, HERDR_PLUGIN_EVENT_JSON=json.dumps(self.event("idle")),
                   HERDR_BIN_PATH=str(fake_herdr))
        children = [subprocess.Popen([sys.executable, str(entry), "event"], env=env)
                    for _ in range(8)]
        self.assertEqual([child.wait(timeout=10) for child in children], [0] * 8)
        state = json.loads(next(storage.state_dir().glob("events-*.json")).read_text())
        self.assertFalse(state["w1:p1"]["armed"])
        play.assert_not_called()

    @patch("noisy_herdr.audio.play")
    def test_malformed_and_unrelated_events_do_nothing(self, play):
        for event in ({}, {"event": "pane_agent_status_changed", "data": []},
                      {"event": "pane_updated", "data": {"pane_id": "w1:p1"}}):
            events.handle_event(event)
        play.assert_not_called()


class SettingsTests(Isolated):
    def test_legacy_attention_migrates_to_questions_and_approvals_only(self):
        path = storage.config_dir() / "settings.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"attention": False, "sound": "glass", "volume": 45}))
        result = storage.settings()
        self.assertFalse(result["questions"])
        self.assertFalse(result["approvals"])
        self.assertFalse(result["narration"])
        self.assertNotIn("attention", result)
        storage.update_settings(questions=True)
        saved = json.loads(path.read_text())
        self.assertTrue(saved["questions"])
        self.assertFalse(saved["approvals"])
        self.assertNotIn("attention", saved)
        self.assertEqual((saved["sound"], saved["volume"]), ("glass", 45))

    def test_persistence_validation_and_atomic_toggle(self):
        self.assertEqual(storage.settings(), storage.DEFAULTS)
        storage.update_settings(sound="glass", volume=35)
        storage.toggle()
        self.assertEqual(storage.settings()["sound"], "glass")
        self.assertFalse(storage.settings()["enabled"])
        with self.assertRaises(ValueError):
            storage.update_settings(volume=101)
        self.assertEqual(storage.settings()["volume"], 35)

    def test_corrupt_config_is_not_silently_replaced(self):
        storage.config_dir().mkdir()
        path = storage.config_dir() / "settings.json"
        path.write_text("{ broken json")
        with self.assertRaises(ValueError):
            storage.toggle()
        self.assertEqual(path.read_text(), "{ broken json")


class AudioTests(unittest.TestCase):
    def test_assets_are_finite_normalized_pcm_with_click_free_edges(self):
        for sound, _, _ in storage.SOUNDS:
            with self.subTest(sound=sound), wave.open(str(audio.SOUND_DIR / (sound + ".wav"))) as stream:
                self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()),
                                 (1, 2, 44100))
                samples = array.array("h", stream.readframes(stream.getnframes()))
                self.assertGreater(max(samples), 10000)
                self.assertLessEqual(max(abs(sample) for sample in samples), 24000)
                self.assertEqual((samples[0], samples[-1]), (0, 0))

    def test_linux_scaling_changes_pcm_amplitude(self):
        with tempfile.TemporaryDirectory() as folder:
            source = audio.SOUND_DIR / "pomodoro.wav"
            target = Path(folder) / "quiet.wav"
            audio.scaled_wav(source, target, 25)
            with wave.open(str(source)) as stream:
                original = array.array("h", stream.readframes(stream.getnframes()))
            with wave.open(str(target)) as stream:
                quiet = array.array("h", stream.readframes(stream.getnframes()))
            self.assertAlmostEqual(max(quiet) / max(original), .25, places=4)

    @patch("noisy_herdr.audio.subprocess.run")
    def test_macos_uses_only_per_process_volume(self, run):
        with patch("noisy_herdr.audio.sys.platform", "darwin"):
            audio.play("pomodoro", 42)
        self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/afplay", "-v", "0.42"])
        run.reset_mock()
        audio.play("pomodoro", 0)
        run.assert_not_called()


class ConfigEditTests(unittest.TestCase):
    def test_preserves_existing_keys_comments_and_following_tables(self):
        original = '[ui.sound]\nenabled = true # my choice\ndone_path = "mine.mp3"\n\n[theme]\nname = "gruvbox"\n'
        expected = original.replace("enabled = true", "enabled = false")
        self.assertEqual(mute_builtin(original), expected)
        self.assertEqual(mute_builtin(expected), expected)

    def test_adds_table_without_overwriting_user_config(self):
        original = 'onboarding = false\n[ui.toast]\ndelivery = "herdr"\n'
        result = mute_builtin(original)
        self.assertTrue(result.startswith(original))
        self.assertIn("[ui.sound]\nenabled = false", result)

    def test_adds_setting_to_existing_empty_table(self):
        result = mute_builtin('[ui.sound]\n# a comment\n[theme]\nname = "gruvbox"\n')
        self.assertIn('[ui.sound]\nenabled = false\n# a comment\n[theme]', result)

    def test_refuses_inline_table_instead_of_corrupting_it(self):
        with self.assertRaises(ValueError):
            mute_builtin('[ui]\nsound = { enabled = true }\n')

    @patch("noisy_herdr.install.install", return_value=0)
    def test_install_cli_dispatch_and_optional_builtin_override(self, install):
        self.assertEqual(main(["install"]), 0)
        install.assert_called_with(False)
        self.assertEqual(main(["install", "--keep-built-in"]), 0)
        install.assert_called_with(True)


class Screen:
    def __init__(self, width=76, height=27):
        self.width, self.height = width, height
        self.writes = []

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.writes = []

    def addnstr(self, y, x, text, limit, style):
        self.writes.append((y, x, text[:limit]))

    def refresh(self):
        pass


class PanelTests(Isolated):
    @patch("noisy_herdr.cli.subprocess.run")
    def test_settings_opens_with_explicit_focus_and_names_its_own_tab(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, json.dumps({"result": {"plugin_pane": {
                "pane": {"tab_id": "w1:t7"}}}}), ""),
            subprocess.CompletedProcess([], 0, "{}", ""),
        ]
        self.assertEqual(open_panel(focus=False), 0)
        self.assertIn("--no-focus", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-4:],
                         ["tab", "rename", "w1:t7", "Noisy Herdr"])

    def test_mouse_targets_toggle_slider_and_close(self):
        panel = Panel(Screen())
        panel.draw()
        self.assertIn("enabled", [hit[3] for hit in panel.hits])
        panel.activate("enabled")
        self.assertFalse(storage.settings()["enabled"])
        _, start, length = panel.slider
        panel.activate("slider", start)
        self.assertEqual(storage.settings()["volume"], 0)
        panel.activate("slider", start + length - 1)
        self.assertEqual(storage.settings()["volume"], 100)
        self.assertFalse(panel.activate("close"))

    def test_sound_selection_previews_at_the_saved_volume(self):
        panel = Panel(Screen())
        with patch.object(panel.preview, "play") as play:
            panel.activate("glass")
        self.assertEqual(storage.settings()["sound"], "glass")
        self.assertEqual(play.call_args.args[0]["volume"], 60)

    def test_small_terminal_has_a_readable_resize_message(self):
        screen = Screen(40, 12)
        panel = Panel(screen)
        panel.draw()
        self.assertIn("Enlarge this panel", " ".join(item[2] for item in screen.writes))


if __name__ == "__main__":
    unittest.main()
