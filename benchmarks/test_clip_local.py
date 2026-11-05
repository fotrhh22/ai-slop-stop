import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.benchmark_clip_local import classification, checked_image


class OfflineAuditTest(unittest.TestCase):
    def test_false_fail_and_missing_are_distinct(self):
        self.assertEqual(classification('PASS', 'FAIL', 'succeeded'), 'FALSE_FAIL')
        self.assertEqual(classification('FAIL', 'PASS', 'succeeded'), 'FALSE_PASS')
        self.assertEqual(classification('PASS', 'FAIL', 'failed'), 'UNAVAILABLE')

    def test_changed_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'image.jpg').write_bytes(b'original')
            expected = hashlib.sha256(b'original').hexdigest()
            self.assertEqual(checked_image(root, 'image.jpg', expected), b'original')
            (root / 'image.jpg').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                checked_image(root, 'image.jpg', expected)


if __name__ == '__main__':
    unittest.main()
