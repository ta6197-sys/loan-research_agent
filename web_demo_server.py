"""기업금융 조사 화면과 로컬 금융 API 프록시."""
from pathlib import Path
import re

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
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
    return FileResponse(HERE/'finance.html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


@app.api_route('/api/{path:path}',methods=['GET','POST'])
async def finance_proxy(path: str,req: Request):
    uid='[0-9a-f]{32}'
    read=[r'health',r'researches',r'research-archives',r'companies',rf'researches/{uid}(?:/(?:summary|events|reports))?',
        rf'runs/{uid}(?:/(?:execution|validations|verified-analysis))?',rf'validation-batches/{uid}(?:/(?:quality|quality-analysis))?',rf'reports/{uid}(?:/(?:view|quality|financial-analysis|compare/{uid}|charts/(?:annual|interim)-[a-z_]+[.]svg))?',
        rf'evidence/{uid}',rf'fragments/{uid}(?:/original)?',rf'sources/{uid}(?:/view)?',r'exports/[0-9a-f]{64}',rf'researches/{uid}/attachment-bundles/[0-9a-f]{{64}}']
    write=[r'researches/general/start',rf'researches/{uid}/(?:inputs|stop|resume|attachment-bundles|plan|archive|restore|general-runs|refresh)',rf'researches/{uid}/quality-exceptions/[0-9a-f]{{64}}/reviews',rf'runs/{uid}/(?:drafts|collect|report)',rf'reports/{uid}/exports']
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


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="127.0.0.1", port=8600)
