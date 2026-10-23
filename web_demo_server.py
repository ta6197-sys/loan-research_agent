"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html이 바닐라 JS 대신 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 되어
있다는 점만 all_total과 다르다. server.py(8100, 기존 통합 테스트 서버)는 전혀
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
from pathlib import Path
import re

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()
app.mount("/static",StaticFiles(directory=HERE/"static"),name="finance-static")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])


@app.middleware("http")
async def check_local_origin(req: Request, call_next):
    origin = req.headers.get("origin")
    allowed = {"http://localhost:8600", "http://127.0.0.1:8600"}
    if ((origin and origin not in allowed) or
            (req.method not in ("GET", "HEAD", "OPTIONS")
             and req.headers.get("sec-fetch-site") == "cross-site")):
        return JSONResponse({"detail": "허용되지 않은 로컬 요청 출처입니다."}, status_code=403)
    response=await call_next(req)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


@app.get("/")
async def index():
    legacy=False
    try:
        async with httpx.AsyncClient(timeout=2,trust_env=False,follow_redirects=False) as client:
            legacy=(await client.get(BACKEND+'/health')).json().get('mode')=='legacy'
    except (httpx.HTTPError,ValueError):pass
    if legacy:return FileResponse(HERE/'web_chat.html')
    return FileResponse(HERE/'finance.html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


@app.get('/legacy')
async def legacy_page():return FileResponse(HERE/'web_chat.html')


@app.api_route('/api/{path:path}',methods=['GET','POST'])
async def finance_proxy(path: str,req: Request):
    uid='[0-9a-f]{32}'
    read=[r'health',r'researches',r'research-archives',r'companies',rf'researches/{uid}(?:/(?:summary|events|reports))?',
        rf'runs/{uid}(?:/(?:execution|validations))?',rf'reports/{uid}(?:/view|/compare/{uid}|/charts/(?:annual|interim)-[a-z_]+[.]svg)?',
        rf'evidence/{uid}',rf'fragments/{uid}(?:/original)?',rf'sources/{uid}(?:/view)?',r'exports/[0-9a-f]{64}',rf'researches/{uid}/attachment-bundles/[0-9a-f]{{64}}']
    write=[r'researches/general/start',rf'researches/{uid}/(?:inputs|stop|resume|attachment-bundles|plan|archive|restore|general-runs|refresh)',rf'runs/{uid}/(?:drafts|collect|report)',rf'reports/{uid}/exports']
    if not any(re.fullmatch(pattern,path) for pattern in (read if req.method=='GET' else write)):
        return JSONResponse({'detail':'허용되지 않은 금융 API 경로입니다.'},status_code=403)
    attachment=path.endswith('/attachment-bundles') and req.method=='POST'
    maximum=110*1024*1024 if attachment else 1024*1024
    body=bytearray()
    async for chunk in req.stream():
        if len(body)+len(chunk)>maximum:return JSONResponse({'detail':'요청 크기를 초과했습니다.'},status_code=413)
        body.extend(chunk)
    content_type=req.headers.get('content-type','application/json') if attachment else 'application/json'
    if attachment and not content_type.startswith('multipart/form-data;'):
        return JSONResponse({'detail':'첨부 묶음 형식이 잘못됐습니다.'},status_code=400)
    try:
        async with httpx.AsyncClient(timeout=240 if attachment else 60,trust_env=False,follow_redirects=False) as client:
            result=await client.request(req.method,BACKEND+'/'+path,params=req.query_params,content=bytes(body),
                headers={'content-type':content_type})
        if len(result.content)>24*1024*1024:raise ValueError('response_size')
        headers={k:result.headers[k] for k in ['content-type','content-disposition','content-security-policy'] if k in result.headers}
        return Response(result.content,status_code=result.status_code,headers=headers)
    except (httpx.HTTPError,ValueError):
        return JSONResponse({'detail':'로컬 조사 서버에 연결하지 못했습니다. 저장된 조사와 게시본은 보존됩니다.'},status_code=502)


@app.get("/health")
async def proxy_health():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/health")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{BACKEND}/chat", content=body,
                              headers={"content-type": "application/json"})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/chat/stream")
async def proxy_stream(message: str, thread_id: str = "web-demo"):
    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{BACKEND}/chat/stream",
                params={"message": message, "thread_id": thread_id},
            ) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="127.0.0.1", port=8600)
