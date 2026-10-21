"""승인된 통합 분석의 공식 원문 목록만 고정 등록한다. DB는 읽기 전용, 외부 요청0."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.cached_case import make_manifest,CachedCaseLibrary
from agent.research_store import safe_payload


def main():
    source=Path(sys.argv[1]).resolve()
    manifest=make_manifest(ROOT,source);text=safe_payload(manifest)
    output=ROOT/'data/research/case_library/sangsin-20261020.json';output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():
        if output.read_text()!=text:raise ValueError('existing_case_manifest_preserved')
    else:
        with output.open('x') as stream:stream.write(text)
    library=CachedCaseLibrary(ROOT,output);library.verify()
    print(json.dumps({'manifest':str(output.relative_to(ROOT)),'case_id':manifest['case_id'],
        'source_count':len(manifest['sources']),'source_database_read_only':True,'external_requests':0},ensure_ascii=False))


if __name__=='__main__':main()
