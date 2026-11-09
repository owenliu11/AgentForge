import time
import unittest


class TestSlow(unittest.TestCase):
    def test_takes_a_while(self):
        time.sleep(60)
