import json
import os
import subprocess
import unittest
from unittest.mock import patch
from wallstreet_quant.groq_windows import request, SCRIPT
from wallstreet_quant.advisor import OpenAIAdvisor


class WindowsGroqTests(unittest.TestCase):
    def test_secret_and_news_not_in_command_and_env_filtered(self):
        payload = {"news": "$(bad); `code` \u20ac"}
        result = subprocess.CompletedProcess([], 0, b'{"choices":[]}', b'')
        with patch.dict(os.environ, {"BITGET_SECRET": "exchange-secret"}), patch("subprocess.run", return_value=result) as run:
            self.assertEqual(request(payload, "test-secret"), {"choices": []})
        args, kwargs = run.call_args
        self.assertNotIn("test-secret", str(args))
        self.assertNotIn(payload["news"], str(args))
        self.assertNotIn("BITGET_SECRET", kwargs["env"])
        self.assertEqual(kwargs["env"]["GROQ_API_KEY"], "test-secret")
        self.assertEqual(json.loads(kwargs["input"]), payload)
        self.assertFalse(kwargs["shell"])
        self.assertIn("-MaximumRedirection 0", SCRIPT)

    def test_failures_do_not_leak_or_retry(self):
        for result in [subprocess.CompletedProcess([], 1, b'{"transport_error":403}', b'secret'),
                       subprocess.CompletedProcess([], 1, b'secret', b'secret'),
                       subprocess.CompletedProcess([], 0, b'x' * 1000001, b''),
                       subprocess.CompletedProcess([], 0, b'\xff', b'')]:
            with self.subTest(code=result.returncode), patch("subprocess.run", return_value=result) as run:
                with self.assertRaises(ValueError) as caught:
                    request({}, "secret")
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(run.call_count, 1)

    def test_timeout_is_safe(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("secret", 135)):
            with self.assertRaisesRegex(ValueError, "timed out") as caught:
                request({}, "secret")
            self.assertNotIn("secret", str(caught.exception))

    def test_windows_advisor_uses_transport_and_validation(self):
        decision = dict(action="skip", confidence=0, notional=0, fraction=0,
            rationale="No evidence", uncertainty="Unverified", evidence_ids=[], material_event=False)
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision)}}]}
        with patch.dict(os.environ, {"WQ_AI_PROVIDER": "groq", "WQ_MODEL": "qwen/qwen3.8-27b", "GROQ_API_KEY": "test"}), patch("wallstreet_quant.advisor.sys.platform", "win32"), patch("wallstreet_quant.groq_windows.request", return_value=response) as transport:
            self.assertEqual(OpenAIAdvisor().decide({"evidence": []}, {})["action"], "skip")
            self.assertEqual(transport.call_count, 1)
            decision["evidence_ids"] = ["invented"]
            response["choices"][0]["message"]["content"] = json.dumps(decision)
            with self.assertRaisesRegex(ValueError, "unknown evidence"):
                OpenAIAdvisor().decide({"evidence": []}, {})
