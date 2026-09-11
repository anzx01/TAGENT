import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.outlook_fields import field_points

class FieldTests(unittest.TestCase):
    def test_window_origin_applied(self):
        points = field_points((50, 200), (50, 260), (1000, 700), (300, 100))
        self.assertEqual(points["recipient"], (470, 300))
        self.assertEqual(points["body"], (410, 420))

    def test_missing_or_reversed_labels_rejected(self):
        for recipient, subject in [(None, (20, 50)), ((20, 100), (20, 50))]:
            with self.assertRaises(ValueError):
                field_points(recipient, subject, (1000, 700))

    def test_small_popup_does_not_click_outside(self):
        with self.assertRaises(ValueError):
            field_points((980, 100), (980, 160), (1000, 700))

if __name__ == "__main__": unittest.main()
