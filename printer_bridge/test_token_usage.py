import unittest
from printer_bridge.renderer import token_number


class TokenUsageTests(unittest.TestCase):
    def test_unreported_is_not_zero(self):
        self.assertEqual(token_number(0, 3, 3), '미계측')
        self.assertEqual(token_number(0, 1, 0), '0')
        self.assertEqual(token_number(150, 4, 3), '150')


if __name__ == '__main__':
    unittest.main()
