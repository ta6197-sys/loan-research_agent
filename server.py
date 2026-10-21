"""조사 저장 기반 + 기존 all_total 학습용 FastAPI 백엔드.

기본 APP_MODE=research는 저장 API와 금융 전용 MCP를 연결한다. 모델을 시작하지 않는다.
APP_MODE=legacy를 명시하면 아래의 기존 실습 기능을 실행한다.

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- LangSmith: 별도 전송 허용 설정이 있을 때만 기존 학습 실행을 추적
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
# 키 존재나 기존 실습 .env의 tracing=true는 금융 자료 전송 동의가 아니다.
ALLOW_EXTERNAL_TRACE = os.environ.get("APP_ALLOW_EXTERNAL_TRACE", "false").lower() == "true"
if not ALLOW_EXTERNAL_TRACE or os.environ.get("APP_MODE", "research") != "legacy":
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1
from agent.research_api import router as research_router
from agent.research_store import ResearchStore, NotFound, Conflict, IntegrityFailure
from agent.session_store import DB_PATH
from agent.finance.runtime import create_finance_client

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        if os.environ.get("APP_ALLOW_EXTERNAL_MODEL", "false").lower() != "true":
            raise RuntimeError("외부 모델 전송이 비활성입니다. 명시적 설정이 필요합니다.")
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    mode = os.environ.get("APP_MODE", "research")
    if mode not in ("research", "legacy"):
        raise RuntimeError("APP_MODE는 research 또는 legacy여야 합니다.")
    app.state.mode = mode
    db_path = Path(os.environ.get("APP_DB_PATH", str(DB_PATH)))
    source_root = Path(os.environ.get("APP_SOURCE_ROOT", str(DB_PATH.parent / "data/research")))
    app.state.research_store = ResearchStore(db_path, source_root)
    from agent.finance.research_followup import FollowupService
    app.state.followups = FollowupService(app.state.research_store)
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
    app.state.research_runtime = ResearchRuntime(app.state.research_store,app.state.followups,case_library=app.state.case_library)
    session_store = SqliteSessionStore(db_path)
    app.state.session_store = session_store
    app.state.instance_id = id(app.state.research_store)
    app.state.deps = None
    app.state.graph = None
    app.state.llm_backend = "disabled"
    app.state.finance_mcp = None
    app.state.finance_status = "not_started"
    mcp = None
    finance = None
    try:
        if mode == "research":
            finance = create_finance_client(db_path, source_root)
            try:
                await finance.start()
                app.state.finance_mcp = finance
                app.state.finance_status = "connected"
            except Exception:
                # 금융 도구 연결 실패는 기존 저장 자료 열람을 막지 않는다.
                app.state.finance_status = "unavailable"
                finance = None
        else:
            backend, fast_llm, quality_llm = _build_llms()
            mcp = MCPClient()
            await mcp.start()
            app.state.llm_backend = backend
            deps = SupervisorDeps(
                fast_llm=fast_llm, quality_llm=quality_llm, mcp=mcp,
                session_store=session_store,
                version=os.environ.get("SUPERVISOR_VERSION", "v2"),
            )
            app.state.deps = deps
            app.state.graph = build_graph(deps) if deps.version == "v2" else None
            app.state.instance_id = id(deps)
        if mode == 'research':app.state.research_runtime.start()
        yield
    finally:
        try:
            await app.state.research_runtime.close()
            await app.state.followups.close()
            if finance is not None:
                await finance.stop()
            if mcp is not None:
                await mcp.stop()
        finally:
            session_store.close()
        if ALLOW_EXTERNAL_TRACE and mode == "legacy":
            from langchain_core.tracers.langchain import wait_for_all_tracers
            wait_for_all_tracers()


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.include_router(research_router)


@app.middleware("http")
async def local_request_boundary(request: Request, call_next):
    allowed = {"http://localhost:8100", "http://127.0.0.1:8100",
               "http://localhost:8600", "http://127.0.0.1:8600"}
    origin = request.headers.get("origin")
    cross_site_write = (request.method not in ("GET", "HEAD", "OPTIONS")
                        and request.headers.get("sec-fetch-site") == "cross-site")
    if (origin and origin not in allowed) or cross_site_write:
        return JSONResponse({"detail": "허용되지 않은 로컬 요청 출처입니다."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


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


def require_legacy():
    if app.state.mode != "legacy":
        raise HTTPException(409, "현재는 조사 저장 기반 모드입니다. 기존 실습 채팅은 APP_MODE=legacy로 실행합니다.")


@app.get("/health")
async def health():
    if app.state.mode == "research":
        return {"status": "ok", "mode": "research", "implementation_stage": "7_cached_case",
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
                "instance_id": app.state.instance_id, "creation_count": CREATION_COUNT}
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name}


@app.get("/version")
async def version():
    return {"mode": app.state.mode, "design_version": "1.0",
            "supervisor_version": app.state.deps.version if app.state.deps else None}


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
    }


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str) -> tuple[dict, str | None]:
    deps = app.state.deps
    history = deps.session_store.load(thread_id)   # Wave 12
    if deps.version == "v2":
        result = await app.state.graph.ainvoke(
            {"query": message, "thread_id": thread_id, "history": history,
             "events": [], "cost_usd": 0.0})
    else:
        result = await run_v1(deps, message, thread_id, history)   # Wave 14
    deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", result["final"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat")
async def chat(req: ChatRequest):
    require_legacy()
    result, run_id = await _handle_chat(req.message, req.thread_id)
    return {"reply": result["final"], "events": result.get("events", []),
            "metadata": _metadata(result, run_id)}


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default"):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    require_legacy()
    deps = app.state.deps

    async def gen():
        history = deps.session_store.load(thread_id)
        if deps.version != "v2":
            result = await run_v1(deps, message, thread_id, history)
            for ev in result["events"]:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': result['final']}, ensure_ascii=False)}\n\n"
        else:
            seen = 0
            final = ""
            # stream_mode를 리스트로 주면 (mode, payload) 튜플이 나온다 — "updates"는
            # 노드가 끝날 때마다의 상태 델타, "custom"은 write_node/chat_answer_node가
            # get_stream_writer()로 실행 중에 즉시 흘려보내는 토큰(AnswerToken) 이벤트다.
            async for mode, payload in app.state.graph.astream(
                    {"query": message, "thread_id": thread_id, "history": history,
                     "events": [], "cost_usd": 0.0}, stream_mode=["updates", "custom"]):
                if mode == "custom":
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    continue
                for node_name, delta in payload.items():
                    for ev in (delta.get("events") or [])[seen:]:
                        ev = {**ev, "node": node_name}
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if delta.get("events"):
                        seen = len(delta["events"])
                    if delta.get("final"):
                        final = delta["final"]
            yield f"data: {json.dumps({'type': 'FinalAnswer', 'reply': final}, ensure_ascii=False)}\n\n"
            deps.session_store.append(thread_id, "user", message)
            deps.session_store.append(thread_id, "assistant", final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
