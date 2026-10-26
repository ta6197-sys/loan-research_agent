"""공장 업종코드 후보를 법인 업종과 분리해 기록한다 (읽기 전용)."""
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'data/research/cohort_official_30392_20261026/factory'
FACTORYON=RAW/'factoryon_20210731.xlsx'
GOYANG=RAW/'goyang_20251210.csv'
DART=ROOT/'doc/finance/KSIC30392_OFFICIAL_CANDIDATES_20261025.json'
OUTPUT=ROOT/'doc/finance/KSIC30392_FACTORY_CANDIDATES_20261026.json'
CATALOGUE=ROOT/'data/research/_catalogue/64b834e3e569fdc3a789e5a7287bd8c20cc431fb9365e012952920142853373c.zip'


def normalized_name(value):
    text=str(value or '').strip()
    text=re.sub(r'^(?:\(주\)|㈜|주식회사)\s*','',text)
    text=re.sub(r'\s*(?:\(주\)|㈜|주식회사)$','',text)
    return re.sub(r'\s+','',text)


def factoryon_rows(path):
    """XLSX 행 스트림. 읽기 전용 openpyxl은 시스템 Python에 포함돼 있다."""
    import openpyxl
    sheet=openpyxl.load_workbook(path,read_only=True,data_only=True).active
    values=sheet.values;headers=next(values)
    for row in values:
        yield dict(zip(headers,row))


def candidate(name,version,source,date,*,possible_code=None,code_role='representative'):
    return {'corp_code':None,'corp_name':str(name or '').strip(),'stock_code':None,
            'industry_code':'30392','industry_classification_system':'KSIC',
            'industry_classification_version':version,'industry_evidence_source':source,
            'industry_evidence_date':date,'industry_scope':'factory',
            'industry_code_role':code_role,'fiscal_end_month':None,
            'candidate_status':'identity_review_required',
            'exclusion_reason':'factory_and_legal_entity_identity_not_proven',
            'possible_dart_corp_code':possible_code,
            'cohort_id':'ksic30392_factory_rev'+version+'_candidates'}


def main():
    dart=json.loads(DART.read_text(encoding='utf-8'))
    by_name={normalized_name(x['corp_name']):x['corp_code'] for x in dart['records']}
    factoryon=[];all_code_count=0
    for row in factoryon_rows(FACTORYON):
        codes=set(re.findall(r'(?<!\d)\d{5}(?!\d)',str(row.get('업종코드') or '')))
        if '30392' in codes:all_code_count+=1
        if str(row.get('대표업종') or '').strip()!='30392':continue
        version=str(row.get('차수') or 'unverified')
        name=row.get('회사명')
        factoryon.append(candidate(name,version,
            'https://www.data.go.kr/data/15088923/fileData.do','2021-07-31',
            possible_code=by_name.get(normalized_name(name))))
    regional=[]
    with GOYANG.open(encoding='cp949',newline='') as stream:
        for row in csv.DictReader(stream):
            codes=set(re.findall(r'(?<!\d)\d{5}(?!\d)',row.get('업종코드') or ''))
            if '30392' not in codes:continue
            name=row['회사명']
            regional.append(candidate(name,'11',
                'https://www.data.go.kr/data/3080741/fileData.do','2025-12-10',
                possible_code=by_name.get(normalized_name(name)),code_role='listed_among_factory_codes'))
    all_records=factoryon+regional
    wanted={normalized_name(x['corp_name']) for x in all_records}
    with zipfile.ZipFile(CATALOGUE) as archive:
        catalogue=ET.fromstring(archive.read('CORPCODE.xml'))
    name_matches={name:[] for name in wanted}
    for row in catalogue:
        name=normalized_name(row.findtext('corp_name'))
        if name in name_matches:
            name_matches[name].append(row.findtext('corp_code'))
    for record in all_records:
        record['catalogue_name_matches']=sorted(name_matches[normalized_name(record['corp_name'])])
    profile_path=RAW/'00665348_company.json'
    if profile_path.exists():
        profile=json.loads(profile_path.read_text(encoding='utf-8'))
        for record in regional:
            if record['corp_name']=='(주)한국토미' and '00665348' in record['catalogue_name_matches']:
                if profile.get('corp_code')=='00665348' and normalized_name(profile.get('corp_name'))==normalized_name(record['corp_name']):
                    record['possible_company_profile_induty_code']=profile.get('induty_code')
                    record['possible_company_profile_sha256']=hashlib.sha256(profile_path.read_bytes()).hexdigest()
                    record['candidate_status']='ksic5_unverified'
                    record['exclusion_reason']='possible_legal_profile_3020_conflicts_with_factory_30392'
    result={'status':'factory_candidates_identity_unverified','as_of':'2026-10-26',
        'factoryon_source_sha256':hashlib.sha256(FACTORYON.read_bytes()).hexdigest(),
        'goyang_source_sha256':hashlib.sha256(GOYANG.read_bytes()).hexdigest(),
        'factoryon_any_30392_row_count':all_code_count,
        'factoryon_representative_30392_count':len(factoryon),
        'goyang_30392_row_count':len(regional),
        'records':all_records,
        'limitation':'공장 단위 업종, 회사명, 공장주소만으로 DART 법인코드 또는 법인 업종을 확정하지 않는다.'}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'factoryon_any':all_code_count,'representative':len(factoryon),
                      'regional':len(regional),'dart_name_hypotheses':sum(bool(x['possible_dart_corp_code'])
                          for x in result['records'])},ensure_ascii=False))


if __name__=='__main__':main()
