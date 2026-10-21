"""실제 로컬 MCP와 FastAPI 프로세스를 재시작해 매핑 이력을 읽는다. 외부 호출 없음."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
BASE=ROOT/'data/research/validation_xbrl/20261021-authorized-pilot'


async def mcp_probe(target, expected):
    from agent.finance.runtime import create_finance_client
    client=create_finance_client(target/'session.db',target/'sources')
    await client.start()
    try:
        result=await client.call_finance('validate_account_mapping',{'run_id':expected['run_id']})
        assert result['status']=='ok' and result['reused']
        mapping_id=expected.get('mapping_batch_id') or expected['batch_id']
        mapping_summary=expected.get('mapping_summary') or expected['summary']
        assert result['batch_id']==mapping_id
        assert result['amount_counts']==mapping_summary['amount_counts']
        output={'status':result['status'],'reused':result['reused'],'batch_id':result['batch_id']}
        if expected.get('numeric_batch_id'):
            numeric=await client.call_finance('validate_numbers',{'run_id':expected['run_id']})
            assert numeric['status']=='ok' and numeric['reused'] and numeric['batch_id']==expected['numeric_batch_id']
            output['numeric_batch_id']=numeric['batch_id']
        return output
    finally:await client.stop()


def http_once(target, expected):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,APP_MODE='research',APP_DB_PATH=str(target/'session.db'),
             APP_SOURCE_ROOT=str(target/'sources'),OPENDART_API_KEY='',
             LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_TRACE='false',
             APP_ALLOW_EXTERNAL_MODEL='false')
    child=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port),
                            '--log-level','error'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        health=None
        for _ in range(100):
            if child.poll() is not None:raise AssertionError('local_server_exited')
            try:
                with urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                    health=json.load(response)
                break
            except OSError:time.sleep(.1)
        assert health and health['llm_backend']=='disabled'
        mapping_id=expected.get('mapping_batch_id') or expected['batch_id']
        mapping_summary=expected.get('mapping_summary') or expected['summary']
        with urlopen(f'http://127.0.0.1:{port}/validation-batches/'+mapping_id,timeout=10) as response:
            batch=json.load(response)
        assert batch['summary']==mapping_summary
        assert len(batch['validations'])==mapping_summary['record_count']
        numeric_count=None
        if expected.get('numeric_batch_id'):
            with urlopen(f'http://127.0.0.1:{port}/validation-batches/'+expected['numeric_batch_id'],timeout=10) as response:
                numeric=json.load(response)
            saved=json.loads((target/'numeric_batch.json').read_text());saved.pop('reused',None)
            assert numeric==saved
            numeric_count=len(numeric['validations'])
        return {'pid':child.pid,'stored_record_count':len(batch['validations']),
                'numeric_record_count':numeric_count,'summary_equal':True,'llm_backend':health['llm_backend']}
    finally:
        child.terminate()
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


def main():
    target=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else sorted((BASE/'analysis').glob('*/result.json'))[-1].parent
    expected=json.loads((target/'result.json').read_text())
    os.environ['OPENDART_API_KEY']=''
    os.environ['LANGCHAIN_TRACING_V2']='false'
    os.environ['LANGSMITH_TRACING']='false'
    mcp=asyncio.run(mcp_probe(target,expected))
    first=http_once(target,expected)
    second=http_once(target,expected)
    assert first['pid']!=second['pid']
    result={'mcp':mcp,'http_processes':[first,second],'external_requests':0,'model_calls':0}
    (target/'runtime_read_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'runtime_check':str((target/'runtime_read_check.json').relative_to(ROOT)),**result},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'status':'runtime_check_failed','error_type':type(error).__name__,'reason':str(error)[:100]}))
        sys.exit(1)
