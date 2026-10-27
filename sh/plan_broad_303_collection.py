"""Freeze a bounded initial 303-family verification sample without API calls."""
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
target=ROOT/'doc/finance/BROAD_303_COLLECTION_PLAN_20261027.json'
audit=json.loads(source.read_text())
already={'00129642','00157539','00305668'}  # 상신브레이크, KB오토시스, 새론오토모티브
eligible=[row for row in audit['records'] if row['candidate_status']=='candidate_only'
          and row['corp_code'] not in already]
eligible.sort(key=lambda row:sha256(('broad-303-initial-20261027:'+row['corp_code']).encode()).hexdigest())
selected=eligible[:24]
payload={'schema_version':'broad-303-collection-plan-1.0','public_cutoff':'2026-10-27',
         'candidate_manifest':'BROAD_303_CANDIDATES_20261027.json',
         'candidate_manifest_sha256':sha256(source.read_bytes()).hexdigest(),
         'selection':'deterministic_hash_order_initial_24_not_a_representative_industry_sample',
         'company_count':len(selected),'max_external_requests_per_company':60,
         'max_total_external_requests':60*len(selected),'model_calls':0,
         'actions':['collect_regular_disclosures','verify_original_api_numeric',
                    'verify_fiscal_period','verify_independent_xbrl',
                    'evaluate_industry_evidence_per_period'],
         'exclusions':['already_verified_elsewhere','unstable_or_missing_303_code'],
         'targets':[{'corp_code':row['corp_code'],'corp_name':row['corp_name'],
                     'stock_code':row['stock_code'],
                     'candidate_observed_code':row['annual_2025']['observed_codes'][0],
                     'annual_2025_receipt':row['annual_2025']['receipt'],
                     'half_2026_receipt':row['half_2026']['receipt']} for row in selected]}
content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
if target.exists() and target.read_text()!=content:
    raise ValueError('immutable_collection_plan_conflict')
target.write_text(content)
print(json.dumps({'company_count':len(selected),'max_total_external_requests':1440},ensure_ascii=False))
