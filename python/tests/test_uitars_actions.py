import sys
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.uitars_actions import parse_action
from services.llm_heal import LLMHealer


class ActionsTest(unittest.TestCase):
    def test_coordinate_modes(self):
        for mode, source in [("1000", "(500,250)"), ("normalized", "(0.5,0.25)"), ("pixels", "(960,270)")]:
            self.assertEqual(parse_action("Action: click(start_box='"+source+"')",1920,1080,mode),
                             {"action":"click","x":960,"y":270})

    def test_reject_code_and_invalid_coordinates(self):
        for source in ["os.system('whoami')", "click(point=__import__('os'))", "click(point='(-1,2)')"]:
            with self.assertRaises((ValueError, TypeError)):
                parse_action(source,1920,1080)

    def test_chinese_input(self):
        self.assertEqual(parse_action("type(content='\u60a8\u597d')",800,600)["text"],"\u60a8\u597d")

    def test_heal_reaches_request(self):
        healer = LLMHealer.__new__(LLMHealer)
        healer._config = {"api_base":"http://localhost/v1","api_key":"test"}
        response = MagicMock()
        response.read.return_value = b'{"choices":[{"message":{"content":"{\\"action\\":\\"wait\\"}"}}]}'
        with patch("urllib.request.urlopen", return_value=response) as call:
            self.assertEqual(healer.heal({},[]), {"action":"wait"})
            call.assert_called_once()

if __name__ == "__main__": unittest.main()
