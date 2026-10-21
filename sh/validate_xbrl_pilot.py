"""확보한 대표 XBRL을 복사한 DB에서만 오프라인 검증한다."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.research_models import SourceInput
from agent.research_store import ResearchStore,now,safe_payload
from agent.finance.xbrl_mapping import XbrlMappingService

BASE=ROOT/'data/research/validation_xbrl/20261021-authorized-pilot'


def main():
    state=json.loads((BASE/'collection.json').read_text())
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=BASE/'analysis'/tag
    target.mkdir(parents=True)
    original_hash=hashlib.sha256((BASE/'session.db').read_bytes()).hexdigest()
    with closing(sqlite3.connect((BASE/'session.db').resolve().as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(target/'session.db')) as dest:source.backup(dest)
    shutil.copytree(BASE/'sources',target/'sources')
    store=ResearchStore(target/'session.db',target/'sources')
    for year,code in [(2025,'11011'),(2026,'11012')]:
        path=ROOT/f'doc/finance/cases/sangsin_brake/raw/financial_{year}_{code}_CFS.json'
        wrapper=json.loads(path.read_text())
        receipt=wrapper['response']['list'][0]['rcept_no']
        store.save_source(state['run_id'],SourceInput(kind='dart',title=path.name,publisher='금융감독원 OpenDART',
            receipt_number=receipt,retrieved_at=wrapper['retrieved_at_utc'],
            reporting_period={'operation':'financials',**wrapper['query']},media_type='application/json',
            rights_status='public_disclosure'),path.read_bytes())
    service=XbrlMappingService(store)
    batch=service.validate_run(state['run_id'])
    (target/'batch.json').write_text(safe_payload(batch))
    restarted=XbrlMappingService(ResearchStore(target/'session.db',target/'sources')).batch(batch['id'])
    assert restarted['validations']==batch['validations']
    reused=service.validate_run(state['run_id'])
    assert reused['reused'] and reused['id']==batch['id']
    with store.connection() as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
        violations=conn.execute('PRAGMA foreign_key_check').fetchall()
    unchanged=hashlib.sha256((BASE/'session.db').read_bytes()).hexdigest()==original_hash
    result={'summary':batch['summary'],'batch_id':batch['id'],'run_id':state['run_id'],
        'source_database_unchanged':unchanged,'restart_read_equal':True,'idempotent':True,
        'integrity':integrity,'foreign_key_violations':len(violations),'external_requests':0,'model_calls':0}
    (target/'result.json').write_text(safe_payload(result))
    print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),**result},ensure_ascii=False))
    return 0 if unchanged and integrity=='ok' and not violations else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(json.dumps({'status':'analysis_stopped','error_type':type(error).__name__,'reason':str(error)[:150]}))
        sys.exit(1)
