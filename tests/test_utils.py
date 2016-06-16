import random

import unittest

import entropy

from bootstrap_cfn import utils


class TestUtils(unittest.TestCase):

    def test_get_random_string_alphanumeric(self):
        """
        TestUtils::test_get_random_string: Test getting a random string
        """
        no_of_tests = 1000
        min_key_length = 24
        max_key_length = 128
        # Generate some keys and test how random they are
        for i in range(no_of_tests):
            length = random.randint(min_key_length, max_key_length)
            alphanumeric = random.choice([True, False])
            if alphanumeric:
                entropy_floor = 0.4
            else:
                entropy_floor = 0.5
            random_string = utils.get_random_string(length, alphanumeric=alphanumeric)
            string_entropy = entropy.shannon_entropy(random_string)
            message = ("TestUtils::test_get_random_string: Test: %s: Generated string length:%s alphanumeric:%s entropy:%s string:%s"
                       % (i, length, alphanumeric, string_entropy, random_string))
            print(message)
            self.assertEqual(len(random_string), length)
            self.assertGreater(string_entropy, entropy_floor, message)
