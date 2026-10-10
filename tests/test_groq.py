import json
import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from wallstreet_quant.advisor import OpenAIAdvisor, provider_settings


class GroqTests(unittest.TestCase):
    def setUp(self):
        platform = patch("wallstreet_quant.advisor.sys.platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)
        self.env = patch.dict(os.environ, {"WQ_AI_PROVIDER": "groq", "WQ_MODEL": "qwen/qwen3.8-27b", "GROQ_API_KEY": "groq-test"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.decision = dict(action="skip", confidence=0.2, notional=0, fraction=0,
            rationale="No evidence", uncertainty="Unverified", evidence_ids=[], material_event=False)

    def response(self, **changes):
        choice = {"finish_reason": "stop", "message": {"content": json.dumps(self.decision)}}
        choice.update(changes)
        return {"id": "test", "choices": [choice], "usage": {"total_tokens": 100}}

    def test_request_and_valid_result(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(self.response()).encode()
        with patch("urllib.request.build_opener", return_value=opener):
            result = OpenAIAdvisor().decide({"evidence": []}, {})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer groq-test")
        body = json.loads(request.data)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertNotIn("input", body)
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["advisor"], "groq_chat")

    def test_incomplete_refusal_tools_and_malformed_rejected(self):
        responses = [self.response(finish_reason="length"),
            self.response(message={"refusal": "no", "content": "{}"}),
            self.response(message={"tool_calls": [{}], "content": "{}"}),
            self.response(message={"content": "not json"}), {"choices": []}]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ValueError):
                OpenAIAdvisor(transport=lambda _: response).decide({"evidence": []}, {})

    def test_unknown_evidence_rejected(self):
        self.decision.update(action="enter", notional=100, material_event=True, evidence_ids=["invented"])
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            OpenAIAdvisor(transport=lambda _: self.response()).decide({"evidence": []}, {})

    def test_key_isolation_and_no_retry(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENAI_API_KEY": "other"}):
            self.assertEqual(provider_settings()[2], "")
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://api.groq.com", 429, "private", {}, None)
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaisesRegex(ValueError, "HTTP 429"):
            OpenAIAdvisor().decide({"evidence": []}, {})
        self.assertEqual(opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
