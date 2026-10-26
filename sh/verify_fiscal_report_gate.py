"""세원정공 보관 원문의 비12월 보고 게이트를 격리 사본에서 재현한다.

원본 DB·원문과 기존 게시본은 읽기만 한다. 직접 선언된 DART 택사노미
참조만 격리 저장소에 추가하며, 결과는 보고서 게시나 코호트 편입이 아니다.
"""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from agent.research_store import ResearchStore,row_data,safe_payload
from agent.finance.dart_service import DartService
from agent.finance.dart_protocol import DartFailure
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.xbrl_mapping import XbrlMappingService,load_reports
from agent.finance.report_context import prepare_context
from agent.finance.financial_analytics import analyze

SOURCE=ROOT/'data/research/validation_fiscal_20261025'
TARGET=ROOT/'data/research/validation_fiscal_gate_20261026'
OUTPUT=ROOT/'doc/finance/FISCAL_REPORT_GATE_AUDIT_20261026.json'
RUN_ID='9739d148e18c485eabeb444274e7d744'
NUMERIC_BATCH_ID='869f774c6f57433ba5a8b11a0549eea8'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    original_hash=sha(SOURCE/'session.db')
    if not TARGET.exists():
        TARGET.mkdir(parents=True)
        shutil.copy2(SOURCE/'session.db',TARGET/'session.db')
        shutil.copytree(SOURCE/'sources',TARGET/'sources')
    store=ResearchStore(TARGET/'session.db',TARGET/'sources')
    with store.connection(write=True) as conn:
        conn.execute("UPDATE research_runs SET status='running' WHERE id=?",(RUN_ID,))
    with store.connection() as conn:
        sources=[row_data(row) for row in conn.execute('SELECT * FROM research_sources')]
    reports,report_failures=load_reports(store,sources)
    existing={(s['metadata']['reporting_period'].get('referring_source_id'),
               s['metadata']['reporting_period'].get('declared_uri')) for s in sources
              if s['metadata']['reporting_period'].get('operation')=='taxonomy_reference'}
    fetched=[];failures=[];dart=DartService(store,'')
    for report in reports.values():
        referrer=report['entity_schema_source_id']
        for entry in report['missing_imports']:
            uri=entry['declared_uri']
            if not uri.startswith(('http://dart.fss.or.kr/Resource/Taxonomy/ifrs/',
                                   'https://dart.fss.or.kr/Resource/Taxonomy/ifrs/')):
                continue
            if (referrer,uri) in existing:continue
            try:
                result=await dart.taxonomy_reference(RUN_ID,uri,referrer)
                fetched.append({'declared_uri':uri,'content_hash':result['content_hash']})
                existing.add((referrer,uri))
            except DartFailure as error:
                failures.append({'declared_uri':uri,'reason':error.kind})
    mapping=XbrlMappingService(store).validate_run(RUN_ID)
    with store.connection() as conn:
        stored_refs=sum(1 for row in conn.execute('SELECT metadata_json FROM research_sources')
            if json.loads(row['metadata_json']).get('reporting_period',{}).get('operation')=='taxonomy_reference')
    batch=NumericValidationService(store).batch(NUMERIC_BATCH_ID)
    context=None;analysis=None;gate_status='withheld';gate_reason=None
    try:
        context=prepare_context(store,batch,mapping)
        gate_status=context['xbrl_report_gate']['status']
        analysis=analyze({'id':'isolated_read_model','content':{'context':context}},batch)
    except ValueError as error:
        gate_reason=str(error)
    if sha(SOURCE/'session.db')!=original_hash:
        raise RuntimeError('archived_numeric_store_changed')
    result={'status':'isolated_non_published_verification','as_of':'2026-10-26',
        'source_db_sha256':original_hash,'numeric_batch_policy':batch['policy_version'],
        'mapping_policy':mapping['policy_version'],'source_xbrl_report_count':len(reports),
        'source_xbrl_report_failures':report_failures,
        'stored_taxonomy_reference_count':stored_refs,'taxonomy_reference_failures':failures,
        'mapping_record_count':mapping['summary']['record_count'],
        'mapping_concept_counts':mapping['summary']['concept_counts'],
        'mapping_context_counts':mapping['summary']['context_counts'],
        'mapping_amount_counts':mapping['summary']['amount_counts'],
        'publication_gate_status':gate_status,'publication_gate_reason':gate_reason,
        'financial_row_count':len(context['financial_rows']) if context else None,
        'verified_financial_row_count':sum(r['status']=='verified' for r in context['financial_rows']) if context else None,
        'analysis_status_counts':dict(Counter(r['status'] for r in analysis['results'])) if analysis else None,
        'comparison_period_ends':context['comparison_period_ends'] if context else None,
        'chart_status_counts':dict(Counter(c['status'] for c in context['charts'])) if context else None,
        'limitation':'원본 원문·API·XBRL의 격리 검증이다. 보고서 게시나 30392 코호트 편입이 아니다.'}
    OUTPUT.write_text(safe_payload(result)+'\n',encoding='utf-8')
    print(safe_payload({'gate':gate_status,'taxonomy_stored':stored_refs,'taxonomy_fetched':len(fetched),
                        'verified_rows':result['verified_financial_row_count']}))
    return 0 if gate_status=='pass' and not failures else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
