"""보관 원문 복사본에서 다섯 내용 묶음의 인용·범위·훼손 차단을 확인한다."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.cached_case import CachedCaseLibrary,preferred_manifest
from agent.finance.public_content import collect_public_content,check_public_quote
from agent.finance.business_review import collect_business_review,check_business_quote
from agent.research_store import ResearchStore,safe_payload,digest,IntegrityFailure,row_data


def main():
    library=CachedCaseLibrary(ROOT,preferred_manifest(ROOT));library.verify();source=library.directory
    before={str(p.relative_to(source)):digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_public_content'/stamp;target.mkdir(parents=True)
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources');store=ResearchStore(target/'session.db',target/'sources')
    with store.connection() as conn:sources=[row_data(r) for r in conn.execute('SELECT * FROM research_sources')]
    findings=collect_public_content(store,sources);run=store.get_run(library.manifest['source_run_id'])
    for finding in findings:
        assert finding['status']=='verified_excerpt',(finding['id'],finding['missing'])
        for quote in finding['quotes']:check_public_quote(store,quote,run['basis_id'])
    business=collect_business_review(store,sources)
    for item in business['items']:
        assert item['status']=='verified_excerpt',(item['id'],item['match_count'])
        check_business_quote(store,item['quote'],run['basis_id'],item['id'])
    bad=deepcopy(findings[0]['quotes'][0]);bad['row']=99999
    try:check_public_quote(store,bad,run['basis_id']);raise AssertionError('forged_row_accepted')
    except IntegrityFailure:pass
    assert before=={str(p.relative_to(source)):digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    result={'status':'pass','findings':findings,'business_review':business,'business_quote_count':len(business['items']),'verified_excerpt_groups':len(findings),
        'quote_count':sum(len(f['quotes']) for f in findings),'forged_row_blocked':True,
        'source_directory':str(source.relative_to(ROOT)),'original_file_count':len(before),'original_changed':0,'original_deleted':0,
        'external_requests':0,'model_calls':0,'financial_confirmation':'incomplete'}
    (target/'result.json').write_text(safe_payload(result));print(safe_payload({'result_file':str((target/'result.json').relative_to(ROOT)),
        'groups':len(findings),'quote_count':result['quote_count'],'status':'pass'}))

if __name__=='__main__':main()
