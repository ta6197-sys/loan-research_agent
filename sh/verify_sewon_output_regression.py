"""세원정공 격리 사본으로 게시 읽기 모델·PDF·ZIP의 출력 일치를 검사한다."""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader

from agent.research_store import ResearchStore, safe_payload
from agent.finance.research_stage05 import Stage05Service
from agent.finance.report_exports import ReportExports
from agent.finance.report_view import build_view

SOURCE = ROOT / 'data/research/validation_fiscal_gate_20261026'
ARCHIVE = ROOT / 'data/research/validation_fiscal_20261025'
TARGET = ROOT / 'data/research/sewon_output_regression_20261026'
OUTPUT = ROOT / 'doc/finance/SEWON_OUTPUT_REGRESSION_20261026.json'
PDF_COPY = ROOT / 'output/pdf/sewon_fiscal_regression_20261026.pdf'
RUN_ID = '9739d148e18c485eabeb444274e7d744'
REQUEST_KEY = 'isolated-sewon-output-regression-20261026'


class ModelUnavailable:
    async def draft(self, *args, **kwargs):
        raise ValueError('local_model_unavailable_in_output_regression')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    original_hash = file_hash(SOURCE / 'session.db')
    archive_hash = file_hash(ARCHIVE / 'session.db')
    gate_audit = json.loads((ROOT / 'doc/finance/FISCAL_REPORT_GATE_AUDIT_20261026.json').read_text(encoding='utf-8'))
    if archive_hash != gate_audit['source_db_sha256']:
        raise ValueError('archived_original_store_hash_changed')
    if not TARGET.exists():
        TARGET.mkdir(parents=True)
        shutil.copy2(SOURCE / 'session.db', TARGET / 'session.db')
        shutil.copytree(SOURCE / 'sources', TARGET / 'sources')
    store = ResearchStore(TARGET / 'session.db', TARGET / 'sources')
    with store.connection(write=True) as conn:
        conn.execute("UPDATE research_runs SET status='finished' WHERE id=? AND status='running'", (RUN_ID,))
    report = await Stage05Service(store, model=ModelUnavailable()).run(RUN_ID, REQUEST_KEY)
    if report.get('status') != 'published':
        raise ValueError('isolated_report_not_published')
    view = build_view(store, report['id'])
    exporter = ReportExports(store)
    pdf_meta = exporter.create(report['id'], 'pdf')
    zip_meta = exporter.create(report['id'], 'markdown_zip')
    pdf_path = store._safe_source_path('_exports/' + pdf_meta['id'] + '/report.pdf')
    zip_path = store._safe_source_path('_exports/' + zip_meta['id'] + '/report.zip')
    pdf_bytes = pdf_path.read_bytes()
    zip_bytes = zip_path.read_bytes()
    reader = PdfReader(pdf_path)
    pdf_text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    with zipfile.ZipFile(zip_path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        markdown = archive.read('report.md').decode('utf-8')
        zip_chart_names = sorted(name for name in archive.namelist() if name.startswith('charts/'))
    expected_chart_names = sorted('charts/' + chart['id'] + '.svg' for chart in view['charts']
                                  if chart['status'] != 'withheld')
    expected = (view['company']['name'], view['public_cutoff'], str(view['version']) + '판')
    if any(value not in markdown or value not in pdf_text for value in expected):
        raise ValueError('ui_pdf_zip_identity_or_cutoff_mismatch')
    if (manifest['report_id'] != view['report_id'] or manifest['version'] != view['version']
            or manifest['view_hash'] != view['view_hash'] or zip_chart_names != expected_chart_names):
        raise ValueError('ui_zip_manifest_or_chart_mismatch')
    withheld = [chart for chart in view['charts'] if chart['status'] == 'withheld']
    withheld_notice = '공식 버전 대응이 미확인이므로 비교 차트를 보류했습니다.'
    if len(withheld) != 2 or withheld_notice not in markdown or withheld_notice not in pdf_text:
        raise ValueError('withheld_chart_not_visible_as_unconfirmed')
    if file_hash(SOURCE / 'session.db') != original_hash:
        raise ValueError('original_isolated_gate_store_changed')
    if file_hash(ARCHIVE / 'session.db') != archive_hash:
        raise ValueError('archived_original_store_changed')
    PDF_COPY.parent.mkdir(parents=True, exist_ok=True)
    PDF_COPY.write_bytes(pdf_bytes)
    result = {'as_of': '2026-10-26', 'status': 'isolated_published_output_regression',
              'source_db_sha256': original_hash, 'archived_original_db_sha256': archive_hash,
              'report_id': report['id'],
              'company': view['company'], 'public_cutoff': view['public_cutoff'],
              'version': view['version'], 'view_hash': view['view_hash'],
              'model_status': view['model_status'], 'pdf_page_count': len(reader.pages),
              'pdf_sha256': hashlib.sha256(pdf_bytes).hexdigest(),
              'zip_sha256': hashlib.sha256(zip_bytes).hexdigest(),
              'chart_status_counts': dict(Counter(chart['status'] for chart in view['charts'])),
              'zip_chart_count': len(zip_chart_names),
              'financial_row_status_counts': dict(Counter(row['status'] for row in view['financial_rows'])),
              'limitation': '격리 사본에서 게시·출력 검증했다. 기존 원본 DB와 실제 사용자 보고서는 변경하지 않았다.'}
    OUTPUT.write_text(safe_payload(result) + '\n', encoding='utf-8')
    print(safe_payload({'report_id': report['id'], 'pdf_pages': len(reader.pages),
                        'zip_charts': len(zip_chart_names), 'withheld_charts': len(withheld)}))


if __name__ == '__main__':
    asyncio.run(main())
