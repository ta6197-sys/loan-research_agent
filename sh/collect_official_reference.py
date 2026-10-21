"""승인된 공식 기준자료 확보. 기업 자료·키 전송 없이 같은 추가20회 예산을 사용한다."""
import asyncio
import json
import os
import re
from pathlib import Path
import sys
from urllib.parse import urljoin,urlsplit
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.research_models import SourceInput
from agent.research_store import ResearchStore,safe_payload,now
from agent.finance.dart_service import DartService
BASE=ROOT/'data/research/validation_xbrl/20261021-expanded-periods'
HOSTS={'www.ifrs.org','ifrs.org','specifications.xbrl.org','www.xbrl.org','dart.fss.or.kr','opendart.fss.or.kr'}


def public_reference_url(url):
    p=urlsplit(url)
    if (p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password
            or re.search(r'(?:^|/)(?:login|signin|sign-in|authorize|oauth|accounts?|my-account)(?:/|$)',p.path,re.I)):
        return False
    try:safe_payload({'url':url})
    except ValueError:return False
    return True

async def collect(url,title):
    p=urlsplit(url)
    if not public_reference_url(url):
        raise ValueError('official_public_https_required')
    state=json.loads((BASE/'collection.json').read_text())
    store=ResearchStore(BASE/'session.db',BASE/'sources');service=DartService(store,'')
    path=BASE/'official_references.json'
    refs=json.loads(path.read_text()) if path.exists() else []
    old=next((r for r in refs if r['declared_url']==url),None)
    if old:return old
    current=url;result=None
    try:
        for _ in range(4):
            check=urlsplit(current)
            if not public_reference_url(current):
                raise ValueError('redirect_outside_official_hosts')
            service._snapshot(state['run_id'],'official_reference_reserved',{'last_public_reference':url},reserve=True)
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=30) as client:
                async with client.stream('GET',current) as response:
                    if 300<=response.status_code<400:
                        target=urljoin(current,response.headers.get('location',''))
                        if target==current:raise ValueError('redirect_loop')
                        target_parts=urlsplit(target)
                        if not public_reference_url(target):
                            result={'status':'access_required_or_external_redirect'};break
                        current=target;continue
                    if response.status_code!=200:
                        result={'status':'http_error','http_status':response.status_code};break
                    content=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content)+len(chunk)>30*1024*1024:raise ValueError('reference_size_limit')
                        content.extend(chunk)
                    media=response.headers.get('content-type','application/octet-stream').split(';')[0]
            source=store.save_source(state['run_id'],SourceInput(kind='official_standard',title=title,
                publisher='IFRS Foundation' if 'ifrs.org' in current else 'XBRL International',
                url=current,retrieved_at=now(),reporting_period={'operation':'official_reference','declared_url':url},
                media_type=media,rights_status='public_reference',
                usage_restrictions=['원문 저작권 고지 보존','내부 근거 확인용. 재배포 권한은 별도 확인','외부 모델·추적 전송 미허용']),bytes(content))
            result={'status':'ok','source_id':source['id'],'content_hash':source['content_hash'],
                    'media_type':media,'byte_size':len(content)}
            break
        if result is None:result={'status':'redirect_limit'}
    except Exception as error:
        result={'status':'stopped','error_type':type(error).__name__}
    record={'declared_url':url,**result}
    refs.append(record);DartService._atomic_write(path,safe_payload(refs).encode())
    usage=store.get_run(state['run_id'])['usage']
    print(json.dumps({**record,'external_requests':usage['external_requests']},ensure_ascii=False),flush=True)
    return record

if __name__=='__main__':
    asyncio.run(collect(sys.argv[1],sys.argv[2]))
