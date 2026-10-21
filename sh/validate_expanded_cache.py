"""추가 수집 원문을 읽기 전용 백업한 DB에서 오프라인 대조한다."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
from agent.research_store import ResearchStore,safe_payload
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.validation_acceptance import assess_stage04
from sh.validate_numbers import reference_comparison


def main():
    collection=sorted((ROOT/'data/research/validation_hardening/live').glob('*/collection.json'))[-1]
    original=collection.parent/'session.db';metadata=json.loads(collection.read_text())
    original_hash=hashlib.sha256(original.read_bytes()).hexdigest()
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=collection.parent/'analysis'/tag;target.mkdir(parents=True)
    with closing(sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target/'session.db')) as copy:source.backup(copy)
    store=ResearchStore(target/'session.db',collection.parent/'sources')
    batch=NumericValidationService(store).validate_run(metadata['run_id'])
    (target/'batch.json').write_text(safe_payload(batch))
    reference=reference_comparison(store,batch,ROOT/'doc/finance/cases/sangsin_brake')
    (target/'reference_comparison.json').write_text(safe_payload(reference))
    acceptance=assess_stage04(batch,reference,optional_balance_dates=('2023-06-30',),store=store)
    (target/'acceptance.json').write_text(safe_payload(acceptance))
    unchanged=hashlib.sha256(original.read_bytes()).hexdigest()==original_hash
    output={'acceptance':acceptance,'batch_id':batch['id'],'run_id':metadata['run_id'],'summary':batch['summary'],
            'reference_counts':reference['counts'],'source_database_unchanged':unchanged,
            'source_root':str(store.source_root.relative_to(ROOT)),'external_requests':0,'model_calls':0,
            'financial_scope_status':'partial_until_semantic_reviews_completed'}
    (target/'result.json').write_text(safe_payload(output))
    print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),
                     'reference_counts':reference['counts'],'acceptance':acceptance,'summary':batch['summary']},ensure_ascii=False),flush=True)
    return acceptance['exit_code'] if unchanged else 1

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'message':'보완 자료 검사 중단. 원본은 보존합니다.'},ensure_ascii=False));sys.exit(1)
