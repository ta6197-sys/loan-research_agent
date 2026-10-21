"""기존 보관 보증 공시를 새 분석 복사본/새 목록에 추가한다. 원본/이전 목록 보존."""
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.cached_case import CachedCaseLibrary,make_manifest
from agent.research_models import SourceInput
from agent.research_store import ResearchStore,digest,safe_payload


def main():
    previous=ROOT/'data/research/case_library/sangsin-20261020.json';library=CachedCaseLibrary(ROOT,previous);library.verify()
    output=previous.with_name('sangsin-20261020-content-v2.json')
    if output.exists():
        current=CachedCaseLibrary(ROOT,output);current.verify();print(safe_payload({'manifest':str(output.relative_to(ROOT)),'source_count':len(current.manifest['sources']),'reused':True}));return
    before=digest(previous.read_bytes());source=library.directory
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage04_integrated'/('content-'+stamp);target.mkdir()
    for path in source.iterdir():
        if path.name.startswith('session.db'):continue
        if path.is_dir():shutil.copytree(path,target/path.name)
        elif path.is_file():shutil.copy2(path,target/path.name)
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    store=ResearchStore(target/'session.db',target/'sources')
    raw_path=ROOT/'doc/finance/cases/sangsin_brake/raw/20260820800110_20260820800110.xml';raw=raw_path.read_bytes()
    meta=SourceInput(kind='dart',title='상신브레이크 타인에 대한 채무보증 결정 2026-08-20',publisher='금융감독원 DART',
        url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260820800110',receipt_number='20260820800110',
        published_at='2026-08-20',retrieved_at='2026-10-20',rights_status='public_disclosure',media_type='text/html',
        reporting_period={'operation':'document_member','corp_code':'00129642','local_archive_hash':digest(raw),
            'local_archive_file':str(raw_path.relative_to(ROOT)),'role':'subsequent_guarantee'},
        usage_restrictions=['공개 공시 원문·발췌의 출처와 기준시점을 보존','외부 모델·추적 전송 금지'])
    store.save_source(library.manifest['source_run_id'],meta,raw)
    manifest=make_manifest(ROOT,target)
    with output.open('x') as stream:stream.write(safe_payload(manifest))
    current=CachedCaseLibrary(ROOT,output);current.verify();assert digest(previous.read_bytes())==before
    print(safe_payload({'manifest':str(output.relative_to(ROOT)),'source_count':len(manifest['sources']),
        'previous_manifest_preserved':True,'external_requests':0,'supplement_hash':digest(raw)}))

if __name__=='__main__':main()
