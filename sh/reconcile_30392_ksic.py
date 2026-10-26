"""불변 후보 판정에서 공식 KSIC 근거를 적용한 새 읽기 스냅샷을 만든다."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.ksic_correspondence import KSIC_11_10_SOURCE, reconcile_30392_candidate

SOURCE = ROOT / 'doc/finance/KSIC30392_IDENTITY_TRIAGE_20261026.json'
OUTPUT = ROOT / 'doc/finance/KSIC30392_REVISION_RECONCILED_20261026.json'
XLSX = ROOT / 'data/research/cohort_official_30392_20261026/ksic/ksic11_10_official.xlsx'


def build():
    if hashlib.sha256(XLSX.read_bytes()).hexdigest() != KSIC_11_10_SOURCE['source_sha256']:
        raise ValueError('official_ksic_source_hash_changed')
    source_bytes = SOURCE.read_bytes()
    source = json.loads(source_bytes)
    records = [reconcile_30392_candidate(row) for row in source['records']]
    if len(records) != 43:
        raise ValueError('candidate_count_changed')
    return {
        'as_of': '2026-10-26',
        'status': 'ksic_meaning_reconciled_identity_unverified',
        'identity_triage_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'ksic_correspondence_source': KSIC_11_10_SOURCE,
        'collection_plan': source['collection_plan'],
        'records': records,
    }


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(OUTPUT)
