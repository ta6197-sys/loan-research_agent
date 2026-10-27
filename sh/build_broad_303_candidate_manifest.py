"""Generate the deterministic, candidate-only 303 audit manifest without API calls."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.finance.broad_303_candidates import build

source = ROOT / 'doc/finance/COHORT_CANDIDATE_AUDIT_20261025.json'
target = ROOT / 'doc/finance/BROAD_303_CANDIDATES_20261027.json'
payload = build(source.read_bytes())
content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
if target.exists() and target.read_text() != content:
    raise ValueError('existing_candidate_manifest_differs')
target.write_text(content)
print(json.dumps({'candidate_count':payload['candidate_count'],
                  'status_counts':payload['status_counts']}, ensure_ascii=False, sort_keys=True))
