import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.outlook_verify import sent_detail_matches

class VerifyTests(unittest.TestCase):
    def test_sent_detail(self):
        self.assertTrue(sent_detail_matches('To: a@example.com Test hello hello.docx Reply Forward 22:24', 'a@example.com', 'Test', 'hello', 'hello.docx'))

    def test_draft_rejected(self):
        self.assertFalse(sent_detail_matches('To: a@example.com Test hello hello.docx Saved draft Reply Forward 22:24', 'a@example.com', 'Test', 'hello', 'hello.docx'))

    def test_missing_attachment_rejected(self):
        self.assertFalse(sent_detail_matches('To: a@example.com Test hello Reply Forward 22:24', 'a@example.com', 'Test', 'hello', 'hello.docx'))

    def test_folder_label_rejected(self):
        self.assertFalse(sent_detail_matches('Sent Items a@example.com Test hello hello.docx', 'a@example.com', 'Test', 'hello', 'hello.docx'))

if __name__ == '__main__': unittest.main()
