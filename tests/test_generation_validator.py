import unittest

from app.runtime.generation_validator import unwrap_state, validate_generation


class GenerationValidatorTests(unittest.TestCase):

    def test_matching_generation(self):

        state_a = {"metadata": {"generation": "same", "schema": 1}}
        state_b = {"metadata": {"generation": "same", "schema": 1}}

        self.assertTrue(validate_generation(state_a, state_b))

    def test_mismatched_generation(self):

        state_a = {"metadata": {"generation": "a", "schema": 1}}
        state_b = {"metadata": {"generation": "b", "schema": 1}}

        self.assertFalse(validate_generation(state_a, state_b))

    def test_missing_metadata(self):

        self.assertFalse(validate_generation({"data": {}}, {"metadata": {"generation": "a", "schema": 1}}))

    def test_schema_mismatch(self):

        state_a = {"metadata": {"generation": "same", "schema": 1}}
        state_b = {"metadata": {"generation": "same", "schema": 2}}

        self.assertFalse(validate_generation(state_a, state_b))

    def test_unwrap_state(self):

        state = {
            "metadata": {"generation": "same", "schema": 1},
            "data": {"value": 1}
        }

        self.assertEqual(unwrap_state(state)["value"], 1)
        self.assertEqual(unwrap_state(state)["_metadata"]["generation"], "same")


if __name__ == "__main__":

    unittest.main()