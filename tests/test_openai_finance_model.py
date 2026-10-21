"""OpenAI 금융 어댑터의 전송 범위·구조화 출력·명시 허용 계약을 검사한다."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from agent.finance.openai_model import OpenAIFinanceModel, finance_model_from_environment

EVIDENCE = [
    {"evidence_id": "F_cash", "text": "현금은 100원이다.", "state": "verified", "fact_ids": ["private"]},
    {"evidence_id": "I_user", "text": "사용자 첨부 원문은 절대 전송하면 안 된다.", "state": "data", "source_id": "private"},
    {"evidence_id": "U_borrowing", "text": "차입 구성은 미확인이다.", "state": "withheld"},
]
VALID = {"facts": [{"evidence_id": "F_cash", "text": "현금은 100원이다."}],
         "unknown_evidence_ids": ["U_borrowing"],
         "questions": [{"question": "차입금의 구성과 만기를 확인할 수 있습니까?", "evidence_ids": ["U_borrowing"]}]}


class OpenAIFinanceModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_and_withheld_summaries_use_strict_schema(self):
        reserved = []
        async def reserve(): reserved.append(True)
        def response(request):
            self.assertEqual("https", request.url.scheme); self.assertEqual("api.openai.com", request.url.host)
            self.assertTrue(reserved); self.assertEqual("Bearer test-key-12345678", request.headers["authorization"])
            payload = json.loads(request.content); self.assertFalse(payload["store"]); self.assertNotIn("tools", payload)
            self.assertEqual("minimal", payload["reasoning_effort"]); self.assertEqual(4096, payload["max_completion_tokens"])
            self.assertTrue(payload["response_format"]["json_schema"]["strict"])
            evidence = json.loads(payload["messages"][1]["content"])["evidence"]
            self.assertEqual(["F_cash", "U_borrowing"], [item["evidence_id"] for item in evidence])
            user_content = payload["messages"][1]["content"]
            self.assertNotIn("첨부", user_content); self.assertNotIn("private", user_content)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(VALID)}}],
                                             "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})
        model = OpenAIFinanceModel(api_key="test-key-12345678", transport=httpx.MockTransport(response))
        result = await model.draft(EVIDENCE, before_request=reserve)
        self.assertEqual(VALID, result["draft"]); self.assertEqual(1, result["usage"]["external_model_requests"])
        self.assertEqual(0, result["external_requests"])

    async def test_secret_or_budget_refusal_prevents_remote_request(self):
        async def refuse(): raise ValueError("model_budget_exhausted")
        model = OpenAIFinanceModel(api_key="test-key-12345678", transport=httpx.MockTransport(lambda _: self.fail("network")))
        with self.assertRaisesRegex(ValueError, "model_budget_exhausted"):
            await model.draft(EVIDENCE, before_request=refuse)
        secret = [{"evidence_id": "F_cash", "text": "sk-not-for-remote-123456", "state": "verified"}]
        with self.assertRaisesRegex(ValueError, "remote_evidence_invalid"):
            await model.draft(secret, before_request=refuse)

    async def test_incomplete_or_invalid_result_is_rejected(self):
        async def reserve(): pass
        for response in (
            httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}),
            httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}),
        ):
            model = OpenAIFinanceModel(api_key="test-key-12345678", transport=httpx.MockTransport(lambda _: response))
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                await model.draft(EVIDENCE, before_request=reserve)


class FinanceModelSelectionTests(unittest.TestCase):
    def test_default_is_local_and_openai_needs_explicit_backend_and_allowance(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("LocalFinanceModel", finance_model_from_environment(".").__class__.__name__)
        with patch.dict(os.environ, {"FINANCE_MODEL_BACKEND": "openai", "OPENAI_API_KEY": "test-key-12345678"}, clear=True):
            with self.assertRaisesRegex(ValueError, "external_finance_model_not_allowed"):
                finance_model_from_environment(".")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env.openai.local").write_text("OPENAI_API_KEY=test-key-12345678\n")
            with patch.dict(os.environ, {"FINANCE_MODEL_BACKEND": "openai", "FINANCE_ALLOW_EXTERNAL_MODEL": "true"}, clear=True):
                model = finance_model_from_environment(directory)
        self.assertEqual("OpenAIFinanceModel", model.__class__.__name__)
        self.assertEqual("gpt-5-mini", model.model)
