"""보완 평가의 게시본을 복사해 같은 인용/미확인의 화면·PDF·ZIP을 확인한다."""
from contextlib import closing
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4
import zipfile
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.report_view import build_view
from agent.finance.report_exports import ReportExports
from agent.research_store import ResearchStore,safe_payload,digest


def main():
    source=Path(sys.argv[1]).resolve()
    if not source.is_relative_to(ROOT/'data/research/evaluation_stage09'):raise ValueError('evaluation_copy_required')
    before={str(p.relative_to(source)):digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_content_outputs'/stamp;target.mkdir(parents=True)
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources');store=ResearchStore(target/'session.db',target/'sources')
    with store.connection() as conn:report_id=conn.execute('SELECT id FROM research_reports ORDER BY version DESC LIMIT 1').fetchone()[0]
    view=build_view(store,report_id);assert len(view['public_findings'])==5
    assert all(f['status']=='verified_excerpt' for f in view['public_findings'])
    service=ReportExports(store);pdf=service.create(report_id,'pdf');bundle=service.create(report_id,'markdown_zip')
    assert pdf['view_hash']==bundle['view_hash']==view['view_hash']
    _,content=service.download(pdf['id']);reader=PdfReader(io.BytesIO(content));text='\n'.join(p.extract_text() for p in reader.pages)
    needles=['24,504,960,486','4,459,391,461','5,497,759,301','4,750,951,157','주휴수당','종속기업투자주식','10,972,896,000','119,125,950,000','2026-08-24','2027-08-20']
    for needle in needles:assert needle in text,needle
    _,data=service.download(bundle['id'])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        md=archive.read('report.md').decode();evidence=json.loads(archive.read('evidence.json'))
        for needle in needles:assert needle in md,needle
        assert len([e for e in evidence['evidence'] if e['id'].startswith('P') and e['use_status']=='verified_excerpt'])==13
        assert not any(name.startswith(('sources/','raw/')) for name in archive.namelist())
    output=ROOT/'output/pdf'/('sangsin-brake-public-content-'+stamp+'.pdf');output.parent.mkdir(exist_ok=True,parents=True);output.write_bytes(content)
    (target/'report.zip').write_bytes(data);(target/'view.json').write_text(safe_payload(view))
    assert before=={str(p.relative_to(source)):digest(p.read_bytes()) for p in source.rglob('*') if p.is_file()}
    result={'status':'pass','source_round':str(source.relative_to(ROOT)),'report_id':report_id,'version':view['version'],
        'report_hash':view['report_hash'],'view_hash':view['view_hash'],'groups':5,'quotes':13,'pdf_pages':len(reader.pages),
        'same_view_pdf_zip':True,'financial_verified':sum(r['status']=='verified' for r in view['financial_rows']),
        'withheld':sum(r['status']=='withheld' for r in view['financial_rows']),'model_calls':0,'external_requests':0,
        'original_file_count':len(before),'original_changed':0,'original_deleted':0,'financial_confirmation':'incomplete',
        'pdf_file':str(output.relative_to(ROOT)),'visual_review':'pending'}
    (target/'result.json').write_text(safe_payload(result));print(safe_payload({'directory':str(target.relative_to(ROOT)),**result}))

if __name__=='__main__':main()
