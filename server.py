"""기업금융 사전조사 FastAPI 백엔드."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from agent.research_api import router as research_router
from agent.research_store import ResearchStore, NotFound, Conflict, IntegrityFailure
from agent.research_paths import DB_PATH
from agent.finance.runtime import create_finance_client

CREATION_COUNT = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    if os.environ.get("APP_MODE", "research") != "research":
        raise RuntimeError("기업금융 조사 모드만 지원합니다.")
    app.state.mode = "research"
    db_path = Path(os.environ.get("APP_DB_PATH", str(DB_PATH)))
    source_root = Path(os.environ.get("APP_SOURCE_ROOT", str(DB_PATH.parent / "data/research")))
    app.state.research_store = ResearchStore(db_path, source_root)
    from agent.finance.openai_model import finance_model_from_environment
    # 기본은 로컬 모델이다. OpenAI는 별도 허용 플래그가 있을 때만 .env.openai.local을 읽는다.
    app.state.finance_model = finance_model_from_environment(Path(__file__).resolve().parent)
    model_class=app.state.finance_model.__class__.__name__
    app.state.finance_model_backend = ("openai" if model_class == "OpenAIFinanceModel" else "ollama" if model_class == "LocalFinanceModel" else "local")
    from agent.finance.research_followup import FollowupService
    app.state.followups = FollowupService(app.state.research_store, app.state.finance_model)
    from agent.finance.cached_case import CachedCaseLibrary,preferred_manifest
    case_manifest=preferred_manifest(Path(__file__).resolve().parent)
    app.state.case_library = None
    app.state.case_library_status = 'not_registered'
    if case_manifest.exists():
        try:
            app.state.case_library=CachedCaseLibrary(Path(__file__).resolve().parent,case_manifest)
            app.state.case_library_status='ready'
        except (ValueError,OSError):app.state.case_library_status='unavailable'
    from agent.finance.research_runtime import ResearchRuntime
    app.state.research_runtime = ResearchRuntime(app.state.research_store,app.state.followups,app.state.finance_model,case_library=app.state.case_library)
    app.state.instance_id = id(app.state.research_store)
    app.state.llm_backend = "disabled"
    app.state.finance_mcp = None
    app.state.finance_status = "not_started"
    finance = None
    try:
        finance = create_finance_client(db_path, source_root)
        try:
            await finance.start()
            app.state.finance_mcp = finance
            app.state.finance_status = "connected"
        except Exception:
            app.state.finance_status = "unavailable"
            finance = None
        app.state.research_runtime.start()
        yield
    finally:
        await app.state.research_runtime.close()
        await app.state.followups.close()
        if finance is not None:
            await finance.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.include_router(research_router)


def _local_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    return response


@app.middleware("http")
async def local_request_boundary(request: Request, call_next):
    allowed = {"http://localhost:8100", "http://127.0.0.1:8100",
               "http://localhost:8600", "http://127.0.0.1:8600"}
    origin = request.headers.get("origin")
    cross_site_write = (request.method not in ("GET", "HEAD", "OPTIONS")
                        and request.headers.get("sec-fetch-site") == "cross-site")
    if (origin and origin not in allowed) or cross_site_write:
        return _local_security_headers(JSONResponse({"detail": "허용되지 않은 로컬 요청 출처입니다."}, status_code=403))
    response = await call_next(request)
    return _local_security_headers(response)


@app.exception_handler(NotFound)
async def missing_record(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Conflict)
@app.exception_handler(IntegrityFailure)
async def conflict_record(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(ValueError)
async def invalid_record(request, exc):
    return JSONResponse({"detail": "저장 요청이 유효하지 않습니다. 인증 정보·출처·값을 확인해주세요."}, status_code=422)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # 기본 오류의 input 필드로 요청 전문/비밀값이 반사되지 않게 한다.
    return JSONResponse({"detail": [{"loc": e["loc"], "type": e["type"]}
                                    for e in exc.errors()]}, status_code=422)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "research", "implementation_stage": "general_company_research",
                "storage": "sqlite", "execution_available": False,
                "llm_backend": "disabled", "external_trace_enabled": False,
                "finance_mcp": app.state.finance_status,
                "local_extraction_available": app.state.finance_status == "connected",
                "numeric_validation_available": app.state.finance_status == "connected",
                "cached_case_draft_available": True,
                "cached_case_start_available": app.state.case_library is not None,
                "case_library_status": app.state.case_library_status,
                "cached_case_followup_available": True,
                "durable_execution_available": True,
                "worker_active": app.state.research_runtime.active,
                "finance_model_backend": app.state.finance_model_backend,
                "instance_id": app.state.instance_id, "creation_count": CREATION_COUNT}


@app.get("/version")
async def version():
    return {"mode": "research", "design_version": "1.0"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
