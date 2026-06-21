from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from redactor import model_redactor, pipeline, regex_redactor


class ModelConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        model_redactor._load_model.cache_clear()

    def test_default_config(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = model_redactor.load_config()
        self.assertEqual(config.model_id, model_redactor.DEFAULT_MODEL_ID)
        self.assertEqual(config.threshold, model_redactor.DEFAULT_THRESHOLD)
        self.assertFalse(config.disable_model)

    def test_custom_threshold_and_model_id(self) -> None:
        env = {
            "PII_REDACTOR_MODEL_ID": "custom/model",
            "PII_REDACTOR_MODEL_THRESHOLD": "0.25",
        }
        with patch.dict(os.environ, env, clear=True):
            config = model_redactor.load_config()
        self.assertEqual(config.model_id, "custom/model")
        self.assertEqual(config.threshold, 0.25)

    def test_invalid_threshold_raises(self) -> None:
        with patch.dict(os.environ, {"PII_REDACTOR_MODEL_THRESHOLD": "not-a-number"}, clear=True):
            with self.assertRaisesRegex(ValueError, "PII_REDACTOR_MODEL_THRESHOLD"):
                model_redactor.load_config()

        with patch.dict(os.environ, {"PII_REDACTOR_MODEL_THRESHOLD": "1.5"}, clear=True):
            with self.assertRaisesRegex(ValueError, "between 0.0 and 1.0"):
                model_redactor.load_config()

    def test_disable_model_skips_model_path(self) -> None:
        text = "Email me at test@example.com"
        expected = regex_redactor.redact(text)

        with patch.dict(os.environ, {"PII_REDACTOR_DISABLE_MODEL": "true"}, clear=True):
            with patch("redactor.model_redactor.redact", side_effect=AssertionError("model path should not run")):
                result = pipeline.redact(text, run_id="test-run")

        self.assertEqual(result.text, expected.text)
        self.assertEqual(result.regex_counts, expected.counts)
        self.assertEqual(result.model_counts, {})
        self.assertEqual(result.device, "disabled")


if __name__ == "__main__":
    unittest.main()
