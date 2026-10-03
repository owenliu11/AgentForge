import unittest

from calc import add, subtract


class TestMath(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 1), 2)

    def test_subtract_fails(self):
        self.assertEqual(subtract(3, 1), 2)
