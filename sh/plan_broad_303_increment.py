"""Freeze the next 12 observed-303 candidates after the initial 24."""
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
initial=ROOT/'doc/finance/BROAD_303_COLLECTION_PLAN_20261027.json'
target=ROOT/'doc/finance/BROAD_303_INCREMENT_PLAN_20261027.json'
audit=json.loads(source.read_text());first=json.loads(initial.read_text())
already={'00129642','00157539','00305668'}
eligible=[row for row in audit['records'] if row['candidate_status']=='candidate_only'
          and row['corp_code'] not in already]
eligible.sort(key=lambda row:sha256(('broad-303-initial-20261027:'+row['corp_code']).encode()).hexdigest())
if [row['corp_code'] for row in eligible[:24]]!=[row['corp_code'] for row in first['targets']]:
    raise ValueError('initial_selection_changed')
selected=eligible[24:36]
if len(selected)!=12:raise ValueError('increment_candidate_count_changed')
payload={'schema_version':'broad-303-increment-plan-1.0','public_cutoff':'2026-10-27',
         'candidate_manifest':source.name,'candidate_manifest_sha256':sha256(source.read_bytes()).hexdigest(),
         'initial_plan':initial.name,'initial_plan_sha256':sha256(initial.read_bytes()).hexdigest(),
         'selection':'same_hash_order_positions_25_to_36_not_representative',
         'company_count':12,'max_external_requests_per_company':60,
         'max_total_external_requests':720,'model_calls':0,
         'targets':[{'corp_code':row['corp_code'],'corp_name':row['corp_name'],
                     'stock_code':row['stock_code'],
                     'candidate_observed_code':row['annual_2025']['observed_codes'][0],
                     'annual_2025_receipt':row['annual_2025']['receipt'],
                     'half_2026_receipt':row['half_2026']['receipt']} for row in selected]}
encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
if target.exists() and target.read_text()!=encoded:raise ValueError('immutable_increment_plan_conflict')
target.write_text(encoded)
print(json.dumps({'company_count':len(selected),'corp_codes':[row['corp_code'] for row in selected],
                  'request_cap':720},ensure_ascii=False))
