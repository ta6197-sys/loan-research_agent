"""보관된 공장 자료와 DART 후보만 사용해 30392 법인 식별 상태를 기록한다."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.candidate_identity import assess_candidate, collection_plan
from sh.audit_factory_30392 import RAW, FACTORYON, GOYANG, normalized_name, factoryon_rows

INPUT = ROOT / 'doc/finance/KSIC30392_FACTORY_CANDIDATES_20261026.json'
DISCLOSURES = ROOT / 'doc/finance/KSIC30392_OFFICIAL_DISCLOSURES_20261026.json'
OUTPUT = ROOT / 'doc/finance/KSIC30392_IDENTITY_TRIAGE_20261026.json'


def factory_evidence():
    rows = []
    for number, row in enumerate(factoryon_rows(FACTORYON), start=2):
        if str(row.get('대표업종') or '').strip() != '30392':
            continue
        rows.append({'source_row': number, 'corp_name': str(row.get('회사명') or '').strip(),
                     'factory_location': {'province': row.get('시도명'), 'city': row.get('시군구명'),
                                          'street_address': None},
                     'industry_classification_version': str(row.get('차수') or 'unverified')})
    with GOYANG.open(encoding='cp949', newline='') as stream:
        for number, row in enumerate(csv.DictReader(stream), start=2):
            if not re.search(r'(?<!\d)30392(?!\d)', str(row.get('업종코드') or '')):
                continue
            rows.append({'source_row': number, 'corp_name': row['회사명'].strip(),
                         'factory_location': {'province': '경기도', 'city': '고양시',
                                              'street_address': row.get('소재지도로명주소') or None},
                         'industry_classification_version': '11'})
    return rows


def main():
    audit = json.loads(INPUT.read_text(encoding='utf-8'))
    if (hashlib.sha256(FACTORYON.read_bytes()).hexdigest() != audit['factoryon_source_sha256']
            or hashlib.sha256(GOYANG.read_bytes()).hexdigest() != audit['goyang_source_sha256']):
        raise ValueError('archived_factory_source_hash_changed')
    candidates = audit['records']
    evidence = factory_evidence()
    if len(evidence) != len(candidates):
        raise ValueError('factory_source_row_count_changed')
    records = []
    for candidate, source in zip(candidates, evidence):
        if (normalized_name(candidate['corp_name']) != normalized_name(source['corp_name'])
                or candidate['industry_classification_version'] != source['industry_classification_version']):
            raise ValueError('factory_source_order_or_version_changed')
        catalogue = candidate['catalogue_name_matches']
        proposed = candidate.get('possible_dart_corp_code')
        checks = {'corp_code': 'pass' if proposed and catalogue == [proposed] else 'unverified',
                  'legal_address': 'unverified', 'factory_location': 'unverified',
                  'official_registration': 'unverified'}
        # 공장 업종과 법인 기업개황의 코드가 다른 것은 업종 근거의 충돌이지 법인 동일성의 증명은 아니다.
        industry = 'conflict' if candidate.get('candidate_status') == 'ksic5_unverified' else 'unverified'
        assessment = assess_candidate(checks, industry_status=industry)
        records.append({**assessment, 'corp_code': None, 'corp_name': candidate['corp_name'],
                        'possible_dart_corp_code': proposed, 'catalogue_name_matches': catalogue,
                        'industry_code': candidate['industry_code'],
                        'industry_classification_version': candidate['industry_classification_version'],
                        'industry_evidence_source': candidate['industry_evidence_source'],
                        'industry_evidence_date': candidate['industry_evidence_date'],
                        'source_row': source['source_row'], 'factory_location': source['factory_location'],
                        'legal_address': None, 'official_registration_evidence': None,
                        'ksic_correspondence_evidence': None,
                        'exclusion_reason': candidate['exclusion_reason'],
                        'candidate_source_sha256': hashlib.sha256(INPUT.read_bytes()).hexdigest()})
    disclosures = json.loads(DISCLOSURES.read_text(encoding='utf-8'))
    official_candidates = ROOT / 'doc/finance/KSIC30392_OFFICIAL_CANDIDATES_20261025.json'
    if disclosures['candidate_source_sha256'] != hashlib.sha256(official_candidates.read_bytes()).hexdigest():
        raise ValueError('archived_dart_candidate_source_hash_changed')
    # 현재 확인된 공장-법인 연결도, 사업·반기보고서가 있는 신규 DART 법인도 없다.
    plan = collection_plan(records, public_cutoff='2026-10-26', request_limit=0)
    output = {'as_of': '2026-10-26', 'status': 'identity_triage_not_cohort_admission',
              'factoryon_source_sha256': hashlib.sha256(FACTORYON.read_bytes()).hexdigest(),
              'goyang_source_sha256': hashlib.sha256(GOYANG.read_bytes()).hexdigest(),
              'disclosure_audit_sha256': hashlib.sha256(DISCLOSURES.read_bytes()).hexdigest(),
              'disclosure_checked_count': disclosures['checked_count'],
              'ksic_revision_policy': '제9·10·11차는 공식 대응표가 없으므로 서로 다른 후보집단으로 유지',
              'ksic_correspondence_source': None,
              'collection_plan': plan, 'records': records}
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    from collections import Counter
    print(json.dumps({'status_counts': dict(Counter(r['identity_status'] for r in records)),
                      'collection_targets': len(plan['target_corp_codes'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
