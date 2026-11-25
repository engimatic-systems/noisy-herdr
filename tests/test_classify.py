"""Regression cases model real Codex dialog/footer shapes, not prose keywords."""
import json
import subprocess
import unittest
from unittest.mock import patch

from noisy_herdr.classify import classify_view, inspect_event


QUESTION = """
  Question 1/1 (1 unanswered)
  Which environment should I use?
  › 1. Local   Use this machine
    2. Staging Use the staging server
  tab to add notes | enter to submit answer | esc to interrupt
"""
FREEFORM = """
  Question 1/1 (1 unanswered)
  What is your preferred project name?
  › Type your answer (optional)
  enter to submit answer | esc to interrupt
"""
APPROVAL = """
  Would you like to run the following command?
  Reason: build the project
  $ make build
  › 1. Yes, proceed (y)
    2. No, and tell Codex what to do differently (esc)
  Press enter to confirm or esc to cancel
"""


class ClassifierTests(unittest.TestCase):
    def classify(self, text, status="blocked", **extra):
        info = {"agent": "codex", "agent_status": status, **extra}
        return classify_view(text, info, status)

    def test_question_tool_with_choices_and_freeform(self):
        self.assertEqual(self.classify(QUESTION).category, "questions")
        self.assertEqual(self.classify(FREEFORM).category, "questions")
        self.assertEqual(self.classify(FREEFORM.replace("enter to", "ctrl+s to")).category, "questions")

    def test_approval_is_separate_from_question_tool(self):
        self.assertEqual(self.classify(APPROVAL).category, "approvals")
        permissions = APPROVAL.replace("run the following command", "grant these permissions")
        self.assertEqual(self.classify(permissions).category, "approvals")

    def test_narration_with_a_question_mark_is_not_a_question_tool(self):
        text = "I will check whether you want to proceed, then build it. Is the cache stale?"
        self.assertEqual(self.classify(text).category, "narration")

    def test_quoted_old_dialog_above_working_footer_is_narration(self):
        text = QUESTION + "\nWorking (12s • esc to interrupt)\n› Ask Codex to do anything"
        self.assertEqual(self.classify(text, "working").category, "narration")

    def test_working_timer_vetoes_false_completion_even_with_sequence(self):
        text = "• I'll inspect the code now.\nWorking (1m 12s • esc to interrupt)\n› Ask Codex to do anything"
        self.assertEqual(self.classify(text, "idle", completion_seq=12, state_change_seq=12).category,
                         "narration")

    def test_real_completion_requires_current_completion_evidence_for_codex(self):
        text = "Implemented and tested.\n› Ask Codex to do anything"
        completed = self.classify(text, "idle", completion_seq=13, state_change_seq=13)
        self.assertEqual((completed.category, completed.identity), ("finished", "13"))
        self.assertEqual(self.classify(text, "idle", completion_seq=12, state_change_seq=13).category,
                         "narration")
        self.assertEqual(self.classify(text, "idle").category, "narration")

    def test_ui_selection_and_notes_do_not_change_question_identity(self):
        first = self.classify(QUESTION)
        changed = self.classify(QUESTION.replace("› 1.", "  1.").replace("  2.", "› 2."))
        self.assertEqual(first.identity, changed.identity)

    def test_stale_dialog_far_above_footer_is_not_interactive(self):
        text = QUESTION + "\n".join("ordinary output " + str(i) for i in range(12))
        self.assertEqual(self.classify(text).category, "narration")

    @patch("noisy_herdr.classify.time.sleep")
    @patch("noisy_herdr.classify.subprocess.run")
    def test_live_inspection_targets_event_pane_and_reads_plain_text(self, run, sleep):
        info = {"pane_id": "w2:p9", "agent": "codex", "agent_status": "blocked"}
        run.side_effect = [subprocess.CompletedProcess([], 0, json.dumps({"result": {"agent": info}})),
                           subprocess.CompletedProcess([], 0, FREEFORM)]
        self.assertEqual(inspect_event("w2:p9", "idle").category, "questions")
        sleep.assert_called_once()
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["get", "w2:p9"])
        self.assertIn("w2:p9", run.call_args_list[1].args[0])

    @patch("noisy_herdr.classify.subprocess.run", side_effect=OSError("closed pane"))
    def test_unavailable_evidence_does_not_guess_at_attention(self, run):
        self.assertIsNone(inspect_event("w1:p1", "blocked"))


if __name__ == "__main__":
    unittest.main()
