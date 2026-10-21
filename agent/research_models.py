"""설계 1.0 저장 계약. 저장 성공은 금융 내용의 검증 통과를 뜻하지 않는다."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Company(Contract):
    name: str = Field(min_length=1, max_length=200)
    corp_code: str | None = Field(default=None, pattern=r"^\d{8}$")
    stock_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{6}$")
    # 공식 법인 식별은 2단계에서 수행한다. 사용자 입력만으로 검증 완료로 만들지 않는다.
    identification_status: Literal["unverified"] = "unverified"
    identification_evidence: str | None = None


class Budget(Contract):
    external_requests: int = Field(default=60, ge=0)
    deepening_actions: int = Field(default=10, ge=0)
    model_calls: int = Field(default=20, ge=0)
    execution_seconds: int = Field(default=1800, ge=1)
    writing_reserve_seconds: int = Field(default=300, ge=0)
    writing_reserve_calls: int = Field(default=4, ge=0)

    @model_validator(mode="after")
    def reserve_within_budget(self):
        if (self.writing_reserve_calls > self.model_calls or
                self.writing_reserve_seconds > self.execution_seconds):
            raise ValueError("작성 여유는 전체 한도 안에 있어야 합니다.")
        return self


class Usage(Contract):
    external_requests: int = Field(default=0, ge=0)
    deepening_actions: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    execution_seconds: int = Field(default=0, ge=0)
    recovery_failures: int = Field(default=0, ge=0)


class BasisInput(Contract):
    purpose: str = Field(min_length=1, max_length=2000)
    public_cutoff: date
    comparison_window: dict
    financial_scope: Literal["OFS_primary_CFS_secondary"] = "OFS_primary_CFS_secondary"
    policy_version: str = "design-1.0"


class ResearchInput(BasisInput):
    company: Company
    request_key: str = Field(min_length=1, max_length=200)
    budget: Budget = Field(default_factory=Budget)


class SourceInput(Contract):
    kind: Literal["dart", "official_ir", "official_standard", "user", "synthetic"]
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str | None = None
    receipt_number: str | None = None
    published_at: str | None = None
    retrieved_at: str
    reporting_period: dict = Field(default_factory=dict)
    media_type: str = "application/octet-stream"
    rights_status: Literal["public_disclosure", "public_reference", "user_authorized", "synthetic"]
    usage_restrictions: list[str] = Field(default_factory=list)
    allow_external_model: bool = False
    allow_external_trace: bool = False
    supersedes_id: str | None = None


class FragmentInput(Contract):
    location: dict = Field(min_length=1)
    text: str
    context: dict = Field(default_factory=dict)
    extraction_warnings: list[str] = Field(default_factory=list)


class FactInput(Contract):
    account: str = Field(min_length=1)
    original_account: str = Field(min_length=1)
    account_id: str | None = None
    mapping_version: str = "unmapped"
    raw_value: str | None
    exact_value: StrictStr | None
    missing_reason: str | None = None
    scope: Literal["OFS", "CFS", "unknown"]
    period: dict = Field(min_length=1)
    currency: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    transformations: list[dict] = Field(default_factory=list)

    @field_validator("exact_value")
    @classmethod
    def exact_decimal(cls, value):
        if value is not None:
            try:
                if not Decimal(value).is_finite():
                    raise ValueError("정확한 유한 십진수 문자열이 필요합니다.")
            except InvalidOperation:
                raise ValueError("정확한 십진수 문자열이 필요합니다.") from None
        return value


class ReportInput(Contract):
    request_key: str = Field(min_length=1, max_length=200)
    # 각 범주를 구별하는 저장 형식. 의미 검증과 실제 게시 기능은 이후 단계다.
    confirmed_facts: list[dict] = Field(default_factory=list)
    interpretations: list[dict] = Field(default_factory=list)
    unknowns: list[dict] = Field(default_factory=list)
    questions: list[dict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(min_length=1)
