"""기존 금융 자료와 새 XBRL을 모두 복사한 저장소에서 통합 검증한다."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.research_models import SourceInput
from agent.research_store import ResearchStore,safe_payload
from agent.finance.xbrl_mapping import XbrlMappingService
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.validation_acceptance import assess_stage04
from sh.validate_numbers import reference_comparison

ORIGINAL=ROOT/'data/research/validation_hardening/live/20261020T233956Z-db52d0dc'
INPUTS=[ROOT/'data/research/validation_xbrl/20261021-authorized-pilot',
        ROOT/'data/research/validation_xbrl/20261021-expanded-periods']


def source_rows(base):
    with closing(sqlite3.connect((base/'session.db').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM research_sources')]


def main():
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage04_integrated'/tag;target.mkdir(parents=True)
    before=hashlib.sha256((ORIGINAL/'session.db').read_bytes()).hexdigest()
    with closing(sqlite3.connect((ORIGINAL/'session.db').resolve().as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(target/'session.db')) as dest:source.backup(dest)
    shutil.copytree(ORIGINAL/'sources',target/'sources')
    store=ResearchStore(target/'session.db',target/'sources')
    state=json.loads((ORIGINAL/'collection.json').read_text());run=state['run_id']
    imported={}
    for base in INPUTS:
        rows=source_rows(base)
        order={'xbrl':0,'xbrl_member':1,'taxonomy_reference':2,'official_reference':3}
        rows=[r for r in rows if json.loads(r['metadata_json'])['reporting_period'].get('operation') in order]
        rows.sort(key=lambda r:order[json.loads(r['metadata_json'])['reporting_period']['operation']])
        for row in rows:
            meta=json.loads(row['metadata_json']);p=dict(meta['reporting_period'])
            for key in ('archive_source_id','referring_source_id'):
                if key in p:p[key]=imported[p[key]]
            meta['reporting_period']=p
            raw=(base/'sources'/row['storage_key']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['content_hash']:raise ValueError('original_source_hash_mismatch')
            new=store.save_source(run,SourceInput(**meta),raw)
            imported[row['id']]=new['id']
    mapping=XbrlMappingService(store).validate_run(run)
    (target/'mapping_batch.json').write_text(safe_payload(mapping))
    numeric=NumericValidationService(store).validate_run(run)
    (target/'numeric_batch.json').write_text(safe_payload(numeric))
    ref=reference_comparison(store,numeric,ROOT/'doc/finance/cases/sangsin_brake')
    acceptance=assess_stage04(numeric,ref,optional_balance_dates=('2023-06-30',))
    for name,data in [('reference_comparison.json',ref),('acceptance.json',acceptance)]:
        (target/name).write_text(safe_payload(data))
    unchanged=hashlib.sha256((ORIGINAL/'session.db').read_bytes()).hexdigest()==before
    result={'run_id':run,'mapping_batch_id':mapping['id'],'numeric_batch_id':numeric['id'],
        'mapping_summary':mapping['summary'],'numeric_summary':numeric['summary'],'acceptance':acceptance,
        'reference_counts':ref['counts'],'source_database_unchanged':unchanged,'external_requests':0,'model_calls':0}
    (target/'result.json').write_text(safe_payload(result))
    print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),
        'mapping_summary':mapping['summary'],'open_compositions':numeric['summary']['open_account_composition_review_count'],
        'calculation_counts':numeric['summary']['calculation_counts'],'required_complete':acceptance['required_complete'],
        'acceptance_exit':acceptance['exit_code'],'source_database_unchanged':unchanged},ensure_ascii=False))
    return acceptance['exit_code'] if unchanged else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(json.dumps({'status':'integration_stopped','error_type':type(error).__name__,'reason':str(error)[:160]}))
        sys.exit(1)
