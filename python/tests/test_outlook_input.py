"""Keyboard input must release all modifiers and reject foreign windows."""
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import outlook_keyboard_compose as compose

class InputTest(unittest.TestCase):
    def test_send_claim_prevents_retry(self):
        with tempfile.TemporaryDirectory() as root:
            directory = compose.message_directory('test@example.com', 'subject', root)
            compose.claim_send(directory, 'test@example.com', 'subject')
            with self.assertRaises(FileExistsError):
                compose.claim_send(directory, 'test@example.com', 'subject')

    def test_folder_label_is_not_message_evidence(self):
        self.assertFalse(compose.fields_visible('Sent Items', 'test@example.com', 'subject', 'hello'))

    def test_shortcut_releases_keys(self):
        with patch.object(compose, "assert_outlook"), patch.object(compose, "key") as key:
            compose.hotkey(17, 13)
            self.assertEqual([c.args for c in key.call_args_list], [(17,), (13,), (13, False), (17, False)])

    def test_foreign_window_prevents_input(self):
        with patch.object(compose, "assert_outlook", side_effect=RuntimeError("foreign")), patch.object(compose, "key") as key:
            with self.assertRaises(RuntimeError):
                compose.hotkey(9)
            key.assert_not_called()

if __name__ == "__main__": unittest.main()
