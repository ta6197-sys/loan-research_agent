"""보관한 공식 자료만 읽어 4단계 의미 검증의 남은 근거를 목록화한다.

원본 DB는 SQLite 읽기 전용으로 열며, 새 진단 결과만 별도 폴더에 쓴다.
완료 판정을 바꾸거나 원문·검증 이력을 수정하지 않는다.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.finance.document_extractor import DocumentExtractor, tag, text
from agent.finance.source_locations import raw_text

VERSION = 'stage04-semantic-inventory-1.1'
BASE = ROOT / 'data/research/validation_hardening/live/20261020T233956Z-db52d0dc'
ANALYSIS = BASE / 'analysis/20261020T235335Z-922c2dd7'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(database=ANALYSIS / 'session.db', source_root=BASE / 'sources'):
    database, source_root = Path(database), Path(source_root)
    before = sha(database)
    documents = []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        batch = conn.execute('SELECT id,summary_json FROM research_validation_batches ORDER BY rowid DESC LIMIT 1').fetchone()
        if not batch:
            raise ValueError('numeric_validation_batch_missing')
        notes = [json.loads(r[0]) for r in conn.execute(
            'SELECT data_json FROM research_validations WHERE batch_id=? AND rule_id=?',
            (batch['id'], 'V12_note_amounts'))]
        for row in conn.execute('SELECT * FROM research_sources ORDER BY id'):
            metadata = json.loads(row['metadata_json'])
            receipt = metadata.get('receipt_number', '')
            member = metadata.get('reporting_period', {}).get('member_name', '')
            if metadata['media_type'] not in ('application/xml', 'text/xml', 'text/html'):
                continue
            if Path(member).name not in {receipt + '.xml', receipt + '.html', receipt + '.htm'}:
                continue
            path = source_root / row['storage_key']
            if not path.resolve().is_relative_to(source_root.resolve()) or sha(path) != row['content_hash']:
                raise ValueError('source_path_or_hash_mismatch')
            content = path.read_bytes()
            parser = DocumentExtractor(content, media_type=metadata['media_type'])
            extracted = parser.extract()
            changed, candidates = [], []
            for table in extracted['tables']:
                joined = ' / '.join(table['section_path'])
                loc = table['location']
                evidence = {'table_number': table['table_number'], 'section_path': table['section_path'],
                            'location': loc, 'scope': table['scope_hint'], 'scope_basis': table['scope_basis']}
                if not loc.get('parser_text_matches_raw'):
                    diff = None
                    if loc.get('raw_index') is not None:
                        original_text = raw_text(parser.raw.inner(loc['raw_index']))
                        nodes = [n for n in parser.root.iter() if tag(n) == 'table' and n.sourceline == loc['line']]
                        if len(nodes) == 1:
                            parsed_text = text(nodes[0])
                            op = next((x for x in difflib.SequenceMatcher(None, original_text, parsed_text,
                                       autojunk=False).get_opcodes() if x[0] != 'equal'), None)
                            if op:
                                diff = {'original_excerpt': original_text[max(0, op[1]-25):op[2]+25],
                                        'parsed_excerpt': parsed_text[max(0, op[3]-25):op[4]+25]}
                    changed.append({**evidence, 'first_difference': diff,
                                    'relevance': 'consolidation_scope_evidence' if '연결대상 종속회사' in joined
                                    else 'other_section_requires_separate_applicability_review'})
                # 읽지 못한 형식을 포함한 발견 목록이다. 여기서 근거로 채택하지 않는다.
                labels = [c['text'] for r in table['rows'] for c in r['cells'] if c['column'] == 1]
                if ('주석' in joined and any('차입' in x for x in labels)) or any(
                        word in joined for word in ('차입금', '리스부채', '종속기업 (연결)')):
                    candidates.append({**evidence, 'unit': table['unit'],
                                       'preamble': table['preamble'],
                                       'row_labels': labels,
                                       'header_rows': [{'row': r['row'], 'cells': r['cells']} for r in table['rows'][:4]],
                                       'evidence_status': 'discovery_only'})
            source_notes = [n for n in notes if n['source_id'] == row['id']]
            documents.append({'source_id': row['id'], 'receipt_number': receipt,
                              'content_hash': row['content_hash'], 'table_count': len(extracted['tables']),
                              'parser_kind': extracted['parser_kind'], 'literal_repair': extracted['literal_repair'],
                              'changed_tables': changed, 'note_candidates': candidates,
                              'note_checks': [{'scope': n['scope'], 'period': n['period'],
                                               'accounts': {a: {'status': v['status'], 'reason': v['reason'],
                                                               'matched_cells': len(v['note_cells'])}
                                                            for a, v in n['account_checks'].items()}}
                                              for n in source_notes]})
    if before != sha(database):
        raise ValueError('source_database_changed')
    changes = [t for d in documents for t in d['changed_tables']]
    return {'version': VERSION, 'input_database': str(database.relative_to(ROOT)),
            'input_database_hash': before, 'source_database_unchanged': True,
            'external_requests': 0, 'model_calls': 0, 'completion_state_changed': False,
            'summary': {'documents': len(documents), 'changed_tables': len(changes),
                        'changed_table_relevance': dict(Counter(x['relevance'] for x in changes)),
                        'note_groups': sum(len(d['note_checks']) for d in documents),
                        'note_account_statuses': dict(Counter(v['status'] for d in documents
                                                     for n in d['note_checks'] for v in n['accounts'].values()))},
            'documents': documents}


def main():
    result = inventory()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    target = ROOT / 'data/research/validation_stage04_semantics' / stamp
    target.mkdir(parents=True)
    (target / 'inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'inventory_file': str((target / 'inventory.json').relative_to(ROOT)),
                      'summary': result['summary'], 'source_database_unchanged': True}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
