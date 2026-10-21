"""금융 조사용 OpenAI 어댑터.

원문 파일, 사용자 후속 입력, 첨부와 DB 경로는 원격 모델에 보내지 않는다. 명시적으로
허용한 경우에도 검증된 수치 문장과 보류 항목의 식별자·설명만 구조화 출력으로 전송한다.
"""
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .local_model import GroundedDraft, SYSTEM, checked_draft


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ALLOWED_STATES = {"verified", "withheld"}
_FEEDBACK = {
    "withheld_evidence_not_preserved", "unsupported_or_modified_fact",
    "invalid_followup_question", "duplicate_fact_reference", "followup_question_missing",
}
_SECRET_MARKER = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|openai[_ -]?api[_ -]?key|api[_ -]?key)", re.I)


def _remote_evidence(evidence):
    """원격 전송 계약을 적용하고, 허용되지 않은 입력은 호출 전에 거부한다."""
    if not isinstance(evidence, list) or len(evidence) > 100:
        raise ValueError("remote_evidence_limit")
    selected = []
    total = 0
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("remote_evidence_invalid")
        state, evidence_id, text = item.get("state"), item.get("evidence_id"), item.get("text")
        if state not in _ALLOWED_STATES:
            # 사용자 입력 등 data 상태는 원격 모델에 보내지 않는다.
            continue
        if (not isinstance(evidence_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", evidence_id)
                or not isinstance(text, str) or not text or len(text) > 2_000
                or _SECRET_MARKER.search(text)):
            raise ValueError("remote_evidence_invalid")
        total += len(text)
        if total > 30_000:
            raise ValueError("remote_evidence_limit")
        selected.append({"evidence_id": evidence_id, "state": state, "text": text})
    if not selected:
        raise ValueError("remote_evidence_empty")
    return selected


class OpenAIFinanceModel:
    """구조화 출력과 사후 검증을 함께 사용하는 좁은 금융 초안 모델 계약."""
    requires_external_model_permission = True

    def __init__(self, model="gpt-5-mini", *, api_key=None, transport=None, allow_archived_public_override=False):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", model):
            raise ValueError("unverified_finance_model")
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.transport = transport
        self.allow_archived_public_override = allow_archived_public_override
        if not self.api_key or self.api_key == "sk-...":
            raise ValueError("openai_api_key_missing")

    async def draft(self, evidence, *, before_request, timeout=180, validation_feedback=None):
        selected = _remote_evidence(evidence)
        if validation_feedback is not None and validation_feedback not in _FEEDBACK:
            raise ValueError("unsupported_validation_feedback")
        system = SYSTEM + (
            "\n원격 모델에는 검증된 수치 문장과 보류 항목만 제공된다. "
            "원문 파일, 사용자 입력, 첨부, URL, 키, 경로를 요구하거나 만들지 않는다."
        )
        if validation_feedback:
            required = [item["evidence_id"] for item in selected if item["state"] == "withheld"]
            system += "\n직전 출력은 " + validation_feedback + " 때문에 거부됐다. unknown_evidence_ids는 반드시 " + json.dumps(required) + "와 정확히 같아야 한다."
        payload = {
            "model": self.model,
            "store": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"evidence": selected}, ensure_ascii=False, separators=(",", ":"))},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "grounded_finance_draft", "strict": True,
                "schema": GroundedDraft.model_json_schema(),
            }},
            # 짧은 근거 목록인데도 추론 토큰으로 JSON이 잘리지 않도록 최소 추론과 여유를 준다.
            "reasoning_effort": "minimal",
            "max_completion_tokens": 4096,
        }
        started = time.monotonic()
        await before_request()
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                     timeout=min(timeout, 180)) as client:
            response = await client.post(OPENAI_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        choice = data.get("choices", [{}])[0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("openai_model_incomplete")
        message = choice.get("message", {})
        if message.get("refusal") or not isinstance(message.get("content"), str):
            raise ValueError("openai_model_refused_or_invalid")
        usage = data.get("usage", {})
        return {"draft": checked_draft(json.loads(message["content"]), evidence), "usage": {
            "model": self.model, "model_calls": 1,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "external_model_requests": 1,
        }, "external_requests": 0, "external_trace": False}


def finance_model_from_environment(project_root):
    """기본은 로컬 모델이며, 외부 연결은 두 개의 명시 설정이 있어야만 켠다."""
    backend = os.environ.get("FINANCE_MODEL_BACKEND", "local")
    if backend == "local":
        from .local_model import LocalFinanceModel
        return LocalFinanceModel()
    if backend != "openai":
        raise ValueError("unsupported_finance_model_backend")
    if os.environ.get("FINANCE_ALLOW_EXTERNAL_MODEL", "false") != "true":
        raise ValueError("external_finance_model_not_allowed")
    local_env = Path(project_root) / ".env.openai.local"
    # 기존 .env/프로세스 환경의 legacy 키를 금융 경로에 우연히 재사용하지 않는다.
    # 전용 파일 값은 환경 변수로 다시 내보내지 않아 자식 프로세스에도 전달되지 않는다.
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if local_env.exists():
        api_key = dotenv_values(local_env).get("OPENAI_API_KEY", "")
    return OpenAIFinanceModel(
        os.environ.get("OPENAI_FINANCE_MODEL", "gpt-5-mini"), api_key=api_key,
        allow_archived_public_override=os.environ.get("FINANCE_ALLOW_ARCHIVED_PUBLIC_EXTERNAL_MODEL", "false") == "true",
    )
