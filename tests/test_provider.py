import os
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from wallstreet_quant.advisor import provider_settings, OpenAIAdvisor


class ProviderTests(unittest.TestCase):
    def test_provider_keys_never_cross(self):
        with patch.dict(os.environ, {"WQ_AI_PROVIDER": "agentrouter", "OPENAI_API_KEY": "direct-only"}, clear=True):
            provider, url, key = provider_settings()
            self.assertEqual(key, "")
            self.assertEqual(url, "https://co.agentrouter.org/v1/responses")
        with patch.dict(os.environ, {"AGENTROUTER_API_KEY": "router-only"}, clear=True):
            self.assertEqual(provider_settings()[2], "")
            self.assertEqual(provider_settings()[0], "openai")

    def test_unknown_provider_rejected(self):
        with patch.dict(os.environ, {"WQ_AI_PROVIDER": "other"}, clear=True):
            with self.assertRaises(ValueError):
                provider_settings()

    def test_request_uses_router_and_does_not_retry_or_leak_error(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://co.agentrouter.org/v1/responses", 401, "secret-body", {}, None)
        with patch.dict(os.environ, {"WQ_AI_PROVIDER": "agentrouter", "AGENTROUTER_API_KEY": "router-test", "WQ_MODEL": "gpt-6-astra"}, clear=True), patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, r"HTTP 401") as caught:
                OpenAIAdvisor().decide({"evidence": []}, {})
            self.assertNotIn("secret-body", str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://co.agentrouter.org/v1/responses")
            self.assertEqual(request.get_header("Authorization"), "Bearer router-test")


if __name__ == "__main__":
    unittest.main()
