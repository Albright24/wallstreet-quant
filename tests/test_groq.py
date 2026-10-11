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
        groq = MagicMock()
        httpx = MagicMock()
        client = groq.Groq.return_value.__enter__.return_value
        client.chat.completions.create.return_value.model_dump.return_value = self.response()
        with patch.dict('sys.modules', {'groq': groq, 'httpx': httpx}):
            result = OpenAIAdvisor().decide({"evidence": []}, {})
        self.assertEqual(groq.Groq.call_args.kwargs['api_key'], 'groq-test')
        self.assertEqual(groq.Groq.call_args.kwargs['max_retries'], 0)
        self.assertFalse(httpx.Client.call_args.kwargs['follow_redirects'])
        body = client.chat.completions.create.call_args.kwargs
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
        class StatusError(Exception):
            status_code = 429
        groq = MagicMock()
        groq.APIStatusError = StatusError
        client = groq.Groq.return_value.__enter__.return_value
        client.chat.completions.create.side_effect = StatusError('private provider text')
        with patch.dict('sys.modules', {'groq': groq, 'httpx': MagicMock()}), self.assertRaisesRegex(ValueError, 'HTTP 429') as error:
            OpenAIAdvisor().decide({"evidence": []}, {})
        self.assertNotIn('private provider text', str(error.exception))
        self.assertEqual(client.chat.completions.create.call_count, 1)



if __name__ == "__main__":
    unittest.main()
