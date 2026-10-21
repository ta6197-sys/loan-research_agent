"""OpenDART 고정 API 전송 계약. 키/인증 URL/제공처 원문 오류는 반환하지 않는다."""
from dataclasses import dataclass
from datetime import date
import io
import json
import logging
from pathlib import PurePosixPath
import re
import stat
from typing import Callable
import xml.etree.ElementTree as ET
import zipfile

import httpx


# 공식 개발가이드의 공통 코드. 알려지지 않은 코드는 재시도하지 않는다.
CODES = {
    "000": ("ok", False),
    "010": ("authentication_error", False),
    "011": ("authentication_error", False),
    "012": ("access_denied", False),
    "013": ("no_data", False),
    "014": ("file_not_found", False),
    "020": ("rate_limited", False),
    "021": ("invalid_request", False),
    "100": ("invalid_request", False),
    "101": ("access_denied", False),
    "800": ("provider_unavailable", True),
    "900": ("provider_error", False),
    "901": ("authentication_error", False),
}
ENDPOINTS = {
    "corporations": ("corpCode.xml", set()),
    "company": ("company.json", {"corp_code"}),
    "disclosures": ("list.json", {"corp_code", "bgn_de", "end_de", "last_reprt_at", "page_no", "page_count"}),
    "financials": ("fnlttSinglAcntAll.json", {"corp_code", "bsns_year", "reprt_code", "fs_div"}),
    "document": ("document.xml", {"rcept_no"}),
    "xbrl": ("fnlttXbrl.xml", {"rcept_no", "reprt_code"}),
    "taxonomy": ("xbrlTaxonomy.json", {"sj_div"}),
}


class DartFailure(Exception):
    def __init__(self, kind, *, code=None, retryable=False, retry_after=None):
        self.kind, self.code = kind, code
        self.retryable, self.retry_after = retryable, retry_after
        # 예외 문자열은 URL/키/제공처 메시지 없이 고정된 분류만 포함한다.
        super().__init__(kind)

    def public(self):
        return {"status": self.kind, "provider_code": self.code, "retryable": self.retryable}


@dataclass(frozen=True)
class DartResponse:
    content: bytes
    media_type: str
    data: dict | None = None


def xml_root(content):
    if b"\x00" in content:
        raise DartFailure("unsupported_encoding")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise DartFailure("unsafe_document")
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        raise DartFailure("parse_error") from None


def zip_members(content, *, max_expanded_bytes, max_members):
    """파일 시스템으로 extract하지 않고 경로/유형/크기/CRC를 검사한다."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > max_members:
                raise DartFailure("archive_limit")
            total = 0
            names = set()
            members = []
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                        or ":" in entry.filename or "\x00" in entry.orig_filename or entry.filename in names):
                    raise DartFailure("unsafe_archive")
                names.add(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or (entry.flag_bits & 1):
                    raise DartFailure("unsafe_archive")
                total += entry.file_size
                if total > max_expanded_bytes:
                    raise DartFailure("archive_limit")
                if not entry.is_dir():
                    data = archive.read(entry)
                    if len(data) != entry.file_size:
                        raise DartFailure("parse_error")
                    members.append((entry.filename, data))
            if not members:
                raise DartFailure("parse_error")
            return members
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError):
        raise DartFailure("parse_error") from None


def validate_params(operation, params):
    if operation not in ENDPOINTS:
        raise DartFailure("invalid_request")
    if set(params) != ENDPOINTS[operation][1]:
        raise DartFailure("invalid_request")
    if "corp_code" in params and not re.fullmatch(r"\d{8}", str(params["corp_code"])):
        raise DartFailure("invalid_request")
    if "rcept_no" in params and not re.fullmatch(r"\d{14}", str(params["rcept_no"])):
        raise DartFailure("invalid_request")
    if operation == "xbrl" and params["reprt_code"] not in {"11011", "11012", "11013", "11014"}:
        raise DartFailure("invalid_request")
    if operation == "taxonomy" and params["sj_div"] not in ({f"BS{i}" for i in range(1,5)} | {f"IS{i}" for i in range(1,5)} | {f"CIS{i}" for i in range(1,5)} | {f"DCIS{i}" for i in range(1,9)} | {f"CF{i}" for i in range(1,5)} | {"SCE1","SCE2"}):
        raise DartFailure("invalid_request")
    if operation == "financials":
        if (not re.fullmatch(r"\d{4}", str(params["bsns_year"])) or int(params["bsns_year"]) < 2015
                or params["reprt_code"] not in {"11011", "11012", "11013", "11014"}
                or params["fs_div"] not in {"OFS", "CFS"}):
            raise DartFailure("invalid_request")
    if operation == "disclosures":
        try:
            start, end = (date.fromisoformat(str(params[k])) for k in ("bgn_de", "end_de"))
            if (start > end or int(params["page_no"]) < 1 or not 1 <= int(params["page_count"]) <= 100
                    or params["last_reprt_at"] not in {"Y", "N"}):
                raise ValueError
        except (ValueError, TypeError):
            raise DartFailure("invalid_request") from None


class _SecretFilter(logging.Filter):
    def __init__(self, secret):
        super().__init__()
        self.secret = secret

    def filter(self, record):
        record.msg = re.sub(r'(crtfc_key=)[^&\s\"\']+', r'\1[REDACTED]',
                            record.getMessage(), flags=re.I).replace(self.secret, "[REDACTED]")
        record.args = ()
        return True


class DartHTTP:
    def __init__(self, api_key, *, max_response_bytes, transport=None):
        self._key = api_key
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self._log_filter = _SecretFilter(api_key) if api_key else None

    async def request(self, operation, params, *, before_request: Callable, timeout=60):
        validate_params(operation, params)
        if not self._key:
            raise DartFailure("missing_configuration")
        endpoint = ENDPOINTS[operation][0]
        # 외부 주소/추가 인증 필드는 호출자가 지정할 수 없다. 리다이렉트도 따르지 않는다.
        endpoint_url = "https://opendart.fss.or.kr/api/" + endpoint
        outgoing = dict(params)
        if operation == "disclosures":
            for key in ("bgn_de", "end_de"):
                outgoing[key] = str(outgoing[key]).replace("-", "")
        outgoing["crtfc_key"] = self._key
        filters = []
        try:
            # HTTPX의 INFO 요청 로그에도 인증 URL이 노출되지 않게 한다.
            if self._log_filter:
                for logger in (logging.getLogger("httpx"), logging.getLogger("httpcore")):
                    logger.addFilter(self._log_filter)
                    filters.append(logger)
            await before_request()
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         follow_redirects=False, timeout=min(timeout, 60)) as client:
                async with client.stream("GET", endpoint_url, params=outgoing) as response:
                    retry_after = response.headers.get("retry-after")
                    if response.status_code == 429:
                        raise DartFailure("rate_limited", retry_after=retry_after)
                    if response.status_code in {408, 500, 502, 503, 504}:
                        raise DartFailure("provider_unavailable", retryable=True, retry_after=retry_after)
                    if 300 <= response.status_code < 400:
                        raise DartFailure("unexpected_redirect")
                    if response.status_code != 200:
                        raise DartFailure("http_error")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > self.max_response_bytes:
                            raise DartFailure("response_limit")
                        content.extend(chunk)
                    decoded = self._decode(bytes(content), operation)
                    if self._key.encode() in content:
                        raise DartFailure("sensitive_response")
                    return decoded
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise DartFailure("network_error", retryable=True) from None
        except httpx.HTTPError:
            raise DartFailure("http_error") from None
        finally:
            for logger in filters:
                logger.removeFilter(self._log_filter)

    @staticmethod
    def _decode(content, operation):
        if operation in {"corporations", "document", "xbrl"} and content.startswith(b"PK\x03\x04"):
            return DartResponse(content, "application/zip")
        if operation in {"corporations", "document", "xbrl"}:
            root = xml_root(content)
            code = root.findtext("status")
            if code is None or not re.fullmatch(r'\d{3}',code):
                raise DartFailure("parse_error")
            kind, retryable = CODES.get(code, ("provider_error", False))
            if kind == "ok":
                raise DartFailure("parse_error")  # ZIP 응답이 필요한 API이므로 XML 성공 응답은 채택하지 않는다.
            raise DartFailure(kind, code=code, retryable=retryable)
        try:
            data = json.loads(content)
        except (ValueError, UnicodeError):
            raise DartFailure("parse_error") from None
        if (not isinstance(data, dict) or not isinstance(data.get("status"), str)
                or not re.fullmatch(r'\d{3}',data['status'])):
            raise DartFailure("parse_error")
        kind, retryable = CODES.get(data["status"], ("provider_error", False))
        if kind != "ok":
            raise DartFailure(kind, code=data["status"], retryable=retryable)
        return DartResponse(content, "application/json", data)
