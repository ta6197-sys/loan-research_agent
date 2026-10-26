"""2025 공식 공장 주소를 30392 후보와 대조하되 법인 식별로 승격하지 않는다."""

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sh.audit_factory_30392 import normalized_name

SOURCE = ROOT / 'data/research/cohort_official_30392_20261026/factory_2025/registered_factories_20251231.zip'
SOURCE_HASH = '5f5699a909601edb3adac4f63c3d2daa2a0d0c26135b0f58f6cdd52673745792'
TRIAGE = ROOT / 'doc/finance/KSIC30392_REVISION_RECONCILED_20261026.json'
DART_PROFILES = ROOT / 'data/research/cohort_official_30392_20261025/company'
OUTPUT = ROOT / 'doc/finance/KSIC30392_FACTORY_ADDRESS_AUDIT_20261026.json'


def comparable_address(value):
    text = re.split(r'\s+\(', str(value or '').strip(), maxsplit=1)[0]
    return re.sub(r'\s+', ' ', text)


def build():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_HASH:
        raise ValueError('factory_2025_source_hash_changed')
    triage_bytes = TRIAGE.read_bytes()
    triage = json.loads(triage_bytes)
    candidates = [row for row in triage['records']
                  if row['cohort_candidate_status'] != 'excluded_ksic9_30392_electrical_equipment']
    if len(candidates) != 42:
        raise ValueError('candidate_count_changed')
    by_name = {}
    for row in candidates:
        by_name.setdefault(normalized_name(row['corp_name']), []).append(row)
    found = {row['source_row']: [] for row in candidates}
    with zipfile.ZipFile(SOURCE) as archive:
        members = [item for item in archive.infolist() if item.filename.endswith('_20251231.csv')]
        if len(members) != 1 or members[0].file_size > 30_000_000:
            raise ValueError('unexpected_factory_archive')
        with archive.open(members[0]) as raw:
            for number, factory in enumerate(csv.DictReader(io.TextIOWrapper(raw, encoding='cp949', newline='')), start=2):
                for candidate in by_name.get(normalized_name(factory['회사명']), []):
                    city = candidate['factory_location']['city']
                    if city and city in factory['공장주소']:
                        found[candidate['source_row']].append({
                            'source_row': number, 'company_name': factory['회사명'],
                            'factory_address': factory['공장주소'],
                            'production': factory['생산품']})
    profiles = {}
    for path in DART_PROFILES.glob('*.json'):
        profile = json.loads(path.read_bytes())
        if profile.get('status') == '000':
            profiles.setdefault(normalized_name(profile['corp_name']), []).append((path, profile))
    records = []
    for candidate in candidates:
        matches = found[candidate['source_row']]
        legal_matches = []
        for path, profile in profiles.get(normalized_name(candidate['corp_name']), []):
            address = comparable_address(profile.get('adres'))
            exact = [row['source_row'] for row in matches
                     if comparable_address(row['factory_address']) == address]
            if exact:
                legal_matches.append({'corp_code': profile['corp_code'],
                                      'profile_source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                      'legal_address': profile['adres'],
                                      'factory_2025_source_rows': exact})
        records.append({'candidate_source_row': candidate['source_row'],
                        'corp_name': candidate['corp_name'],
                        'industry_classification_version': candidate['industry_classification_version'],
                        'identity_status': candidate['identity_status'],
                        'factory_2025_same_name_city_matches': matches,
                        'dart_legal_address_exact_matches': legal_matches,
                        'next_stage_allowed': False})
    return {'as_of': '2026-10-26', 'factory_source_date': '2025-12-31',
            'factory_source_url': 'https://www.data.go.kr/data/15105482/fileData.do',
            'factory_source_sha256': SOURCE_HASH,
            'candidate_source_sha256': hashlib.sha256(triage_bytes).hexdigest(),
            'status': 'address_clues_only_not_legal_identity', 'records': records,
            'limitation': '2025 원본에는 공장등록번호·업종코드·사업자등록번호가 없어 2021 업종 행이나 DART 법인과의 동일성을 확정할 수 없다.'}


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(OUTPUT)
