"""Build exploratory peer rows from a completed, unpublished verified run.

The existing run read model enforces correction, raw, numeric and XBRL gates.
This layer checks each prospective dataset row's identity, industry evidence,
period and feature provenance again before passing it to peer_dataset.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from agent.research_store import encode
from .numeric_validation import NumericValidationService
from .peer_dataset import FEATURES, FEATURE_POLICY_VERSION
from .report_context import fiscal_periods

VERSION = 'verified-unpublished-peer-1.0'
INDUSTRY_PATTERN = re.compile(rb'<dart-gcd:StandardIndustryCode\b[^>]*>(\d{3,5})</dart-gcd:StandardIndustryCode>')


def _source_manifest(store, source_ids, cutoff):
    output=[]
    for identifier in sorted(source_ids):
        source=store.get_source(identifier)
        meta=source['metadata']
        operation=meta.get('reporting_period',{}).get('operation')
        public_at=meta.get('published_at') or (meta.get('retrieved_at') if operation=='company' else None)
        if meta.get('kind')!='dart' or not public_at or public_at[:10]>cutoff:
            raise ValueError('source_publication_or_kind_unconfirmed')
        if not meta.get('receipt_number') and operation!='company':
            raise ValueError('source_receipt_missing')
        output.append({'source_id':identifier,'source_hash':source['content_hash'],
                       'receipt':meta.get('receipt_number') or '',
                       'public_at':public_at[:10]})
    return output


def _industry_sources(store, run_id, corp_code, target_reports):
    """Require an XBRL entity member with one exact code for each selected report."""
    wanted={item['rcept_no'] for item in target_reports}
    found={}
    with store.connection() as conn:
        rows=conn.execute('SELECT id FROM research_sources WHERE run_id=?',(run_id,)).fetchall()
    for row in rows:
        source=store.get_source(row['id']);meta=source['metadata'];period=meta.get('reporting_period',{})
        name=period.get('member_name','')
        if (period.get('operation')!='xbrl_member' or not name.startswith('entity'+corp_code+'_')
                or not name.endswith('.xbrl') or meta.get('receipt_number') not in wanted):
            continue
        raw=store.source_content(source['id'])
        codes={match.decode() for match in INDUSTRY_PATTERN.findall(raw)}
        receipt=meta['receipt_number']
        if len(codes)!=1 or receipt in found:
            found[receipt]=None
        else:
            found[receipt]={'code':next(iter(codes)),'source_id':source['id']}
    return {receipt:found.get(receipt) for receipt in wanted}


def _accounting_evidence(store, run_id, receipt):
    phrase='한국채택국제회계기준'.encode()
    with store.connection() as conn:
        rows=conn.execute('SELECT id FROM research_sources WHERE run_id=?',(run_id,)).fetchall()
    for row in rows:
        source=store.get_source(row['id']);meta=source['metadata']
        if (meta.get('receipt_number')!=receipt or
                meta.get('reporting_period',{}).get('operation')!='document_member'):
            continue
        raw=store.source_content(source['id']);offset=raw.find(phrase)
        if offset>=0:
            return {'source_id':source['id'],'byte_start':offset,'byte_end':offset+len(phrase),
                    'excerpt_sha256':sha256(raw[offset:offset+len(phrase)]).hexdigest()}
    return None


def build_run(store, run_id, candidate):
    """Return eligible observations and explicit exclusions; never publish a report."""
    if candidate.get('candidate_status')!='candidate_only':
        raise ValueError('industry_candidate_not_supported')
    stable_codes=set(candidate['annual_2025']['observed_codes']+candidate['half_2026']['observed_codes'])
    if len(stable_codes)!=1 or not next(iter(stable_codes)).startswith('303'):
        raise ValueError('stable_303_code_required')
    stable_code=next(iter(stable_codes))
    run=store.get_run(run_id)
    collection=run.get('trigger',{}).get('collection_result',{})
    if run['status']!='finished' or run.get('trigger',{}).get('kind')!='general_company' or collection.get('next_phase')!='numeric_and_xbrl_validation':
        raise ValueError('complete_general_verified_run_required')
    with store.connection() as conn:
        basis=conn.execute('SELECT research_id,data_json FROM research_bases WHERE id=?',(run['basis_id'],)).fetchone()
    research=store.get_research(basis['research_id'])
    company=research['company']
    if company['corp_code']!=candidate['corp_code']:
        raise ValueError('candidate_company_mismatch')
    cutoff=json.loads(basis['data_json'])['public_cutoff']
    identity_id=collection.get('identity_source_id')
    identity_source=store.get_source(identity_id)
    if (identity_source['run_id']!=run_id or
            identity_source['metadata'].get('reporting_period',{}).get('operation')!='company'):
        raise ValueError('company_identity_source_mismatch')
    identity=json.loads(store.source_content(identity_id))
    try:fiscal_month=int(identity.get('acc_mt'))
    except (ValueError,TypeError):raise ValueError('identity_industry_or_fiscal_month_unconfirmed') from None
    if (identity.get('corp_code')!=company['corp_code'] or
            not str(identity.get('induty_code','')).startswith('303') or
            not 1<=fiscal_month<=12 or
            collection.get('fiscal_period',{}).get('month')!=fiscal_month):
        raise ValueError('identity_industry_or_fiscal_month_unconfirmed')
    target_reports=collection['target_reports']
    periods=fiscal_periods(target_reports,fiscal_month)
    xbrl=_industry_sources(store,run_id,company['corp_code'],target_reports)
    from agent.research_api import _run_verified_analysis_payload
    preview=_run_verified_analysis_payload(store,run_id)
    if preview['xbrl_gate']['status']!='pass':
        raise ValueError('independent_xbrl_gate_not_passed')
    rows=[row for row in preview['financial_rows'] if row['status']=='verified']
    by_fact={fact_id:row for row in rows for fact_id in row.get('fact_ids',[])}
    period_receipt={(item['year'],item['report_code']):item['rcept_no'] for item in target_reports}
    records=[];excluded=[]
    for period in periods:
        end=period['end'];receipt=period_receipt[(period['year'],period['report_code'])]
        if xbrl[receipt] is None:
            excluded.append({'corp_code':company['corp_code'],'period_end':end,
                             'reason':'xbrl_industry_report_missing_or_conflicting'})
            continue
        if xbrl[receipt]['code']!=stable_code:
            excluded.append({'corp_code':company['corp_code'],'period_end':end,'reason':'industry_code_differs_from_stable_candidate',
                             'observed_code':xbrl[receipt]['code'],'candidate_code':stable_code})
            continue
        accounting=_accounting_evidence(store,run_id,receipt)
        if accounting is None:
            excluded.append({'corp_code':company['corp_code'],'period_end':end,
                             'reason':'accounting_standard_original_unconfirmed'})
            continue
        for scope in ('OFS','CFS'):
            same=[row for row in rows if row['scope']==scope and row['end']==end]
            features={};source_ids={identity_id,xbrl[receipt]['source_id'],accounting['source_id']}
            for metric in preview['financial_metrics']:
                if (metric['status']!='supported' or metric['name'] not in FEATURES or
                        metric['scope']!=scope or (metric.get('period') or {}).get('end',
                            (metric.get('period') or {}).get('as_of'))!=end):
                    continue
                fact_ids=set(metric.get('input_fact_ids',[]))
                if not fact_ids or not fact_ids.issubset(by_fact):continue
                inputs=list({id(by_fact[fact_id]):by_fact[fact_id] for fact_id in fact_ids}.values())
                if (any(row['scope']!=scope for row in inputs)
                        or {v for row in inputs for v in row['validation_ids']}!=set(metric.get('input_validation_ids',[]))
                        or {v for row in inputs for v in row['source_ids']}!=set(metric.get('source_ids',[]))):
                    continue
                refs=[{key:row[key] for key in ('account','period','fact_ids','validation_ids','source_ids')}
                      for row in sorted(inputs,key=lambda item:(item['end'],item['account']))]
                features[metric['name']]={'status':'supported','value':metric['value'],
                                          'evidence_refs':refs}
                source_ids.update(metric['source_ids'])
            current={row['account']:row for row in same}
            for account,name in (('inventory','inventory_to_assets'),('receivables','receivables_to_assets')):
                if account not in current or 'assets' not in current:continue
                try:
                    numerator=Decimal(current[account]['value']);denominator=Decimal(current['assets']['value'])
                except (InvalidOperation,KeyError):continue
                if denominator<=0:continue
                inputs=[current[account],current['assets']]
                features[name]={'status':'supported','value':str(numerator/denominator*100),
                                'evidence_refs':[{key:row[key] for key in
                                    ('account','period','fact_ids','validation_ids','source_ids')} for row in inputs]}
                source_ids.update(v for row in inputs for v in row['source_ids'])
            if not features:
                excluded.append({'corp_code':company['corp_code'],'period_end':end,'scope':scope,
                                 'reason':'no_supported_traceable_features'})
                continue
            manifest=_source_manifest(store,source_ids,cutoff)
            public_at=max(item['public_at'] for item in manifest)
            records.append({'corp_code':company['corp_code'],'industry_code':'303',
                'industry_observed_code':stable_code,'industry_system':'OpenDART_XBRL_observed_family',
                'industry_version':'unconfirmed','accounting_standard_evidence':accounting,
                'period_start':period['start'],'period_end':end,
                'period_kind':'duration','report_period_kind':period['period_kind'],
                'scope':scope,'public_cutoff':cutoff,'source_receipt':receipt,
                'source_hash':sha256(encode(manifest).encode()).hexdigest(),
                'source_public_at':public_at,'validation_batch_id':preview['validation_batch_id'],
                'mapping_batch_id':preview['mapping_batch_id'],'feature_policy_version':FEATURE_POLICY_VERSION,
                'account_definition_version':'verified-peer-account-definitions-1.0',
                'peer_group':'exploratory_observed_303_family','correction_status':'current_verified',
                'features':features,'source_manifest':manifest,'report_id':None,
                'verification_gates':{'original_api':'pass','actual_period':'pass','fiscal_month':'pass',
                                      'independent_xbrl':'pass'},
                'data_origin':'verified_public_disclosure','accounting_standard':'K-IFRS',
                'currency':'KRW','run_id':run_id})
    return {'schema_version':VERSION,'run_id':run_id,'candidate_code':stable_code,
            'records':records,'excluded':excluded,
            'limitation':'미게시 검증 수치의 탐색적 303 계열 분석 후보이며 KSIC 개정 차수와 법인 사업 구성은 확정하지 않음'}
