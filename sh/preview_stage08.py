"""검증 복사본만 사용해 로컬 화면을 미리 본다. 사용자 프로세스를 종료하지 않는다."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
target=Path(sys.argv[1]).resolve()
if not target.is_relative_to(ROOT/'data/research/validation_stage08'):raise ValueError('analysis_only')
for port in (8100,8600):
 with socket.socket() as s:s.bind(('127.0.0.1',port))
env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),APP_SOURCE_ROOT=str(target/'sources'),
 OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
processes=[]
try:
 for name,command in [('backend',[sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port','8100']),
                      ('frontend',[sys.executable,'-m','uvicorn','web_demo_server:app','--host','127.0.0.1','--port','8600'])]:
  log=(target/(name+'-preview.log')).open('w');processes.append(subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log));log.close()
 print('Finance preview: http://localhost:8600 — analysis copy only',flush=True)
 while all(p.poll() is None for p in processes):time.sleep(1)
finally:
 for p in processes:
  if p.poll() is None:p.terminate()
 for p in processes:
  try:p.wait(timeout=10)
  except subprocess.TimeoutExpired:p.kill();p.wait()
