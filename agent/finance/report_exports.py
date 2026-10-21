"""게시 버전의 읽기 모델로 PDF/Markdown ZIP을 만든다. 원문/첨부는 포함하지 않는다."""
from functools import partial
from html import escape
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import os
import zipfile

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
from reportlab.graphics.shapes import Drawing,Rect,Line,String

from agent.research_store import Conflict,IntegrityFailure,digest,safe_payload
from .report_view import build_view,chart_svg,text

ROOT=Path(__file__).resolve().parents[2]
RENDERER='finance-export-1.3'
FONT_LOCK=threading.Lock()


def md(value):
    return re.sub(r'([\\`*_{}\[\]()<>#!|])',r'\\\1',text(value)).replace('\n',' ')


def scope_name(scope):return '별도' if scope=='OFS' else '연결' if scope=='CFS' else '보증 공시' if scope=='guarantee_disclosure' else '사업 공시'


def tables(view):
    from .report_context import LABELS,PERIOD_ENDS
    flow={'revenue','operating_profit','net_profit','operating_cashflow'}
    lookup={(r['scope'],r['end'],r['account']):r for r in view['financial_rows']}
    result=[]
    for scope in ('OFS','CFS'):
        for title,ends,accounts in [
            ('연간 손익·현금흐름 (연초부터 누적)',PERIOD_ENDS[:3],[a for a in LABELS if a in flow]),
            ('연말 재무상태 (기간 말 잔액)',PERIOD_ENDS[:3],[a for a in LABELS if a not in flow]),
            ('반기 손익·현금흐름 (동기간 누적)',PERIOD_ENDS[3:],[a for a in LABELS if a in flow]),
            ('반기 재무상태 (직전 연말·반기 말 잔액)',('2025-12-31','2026-06-30'),[a for a in LABELS if a not in flow]),
            ('전년 반기말 재무상태 (보조·주요 비교 아님)',('2025-06-30',),[a for a in LABELS if a not in flow])]:
            rows=[]
            for account in accounts:
                cells=[]
                for end in ends:
                    row=lookup.get((scope,end,account))
                    if row is None:cells.append({'status':'withheld','display':'미확인','evidence_ids':[]});continue
                    value=row.get('value') if row['status']=='verified' else None
                    if value is not None:
                        from decimal import Decimal
                        display=format(Decimal(value),',f')
                    else:display='미확인'
                    cells.append({**row,'display':display})
                rows.append({'label':LABELS[account],'cells':cells})
            result.append({'title':scope_name(scope)+' '+title,'scope':scope,'ends':list(ends),'rows':rows})
    return result


def markdown(view):
    lines=[f"# {md(view['company']['name'])} 운전자금 사전조사 초안",'',
        f"보고서 {view['version']}판 · 공개 기준 {view['public_cutoff']} · 별도 주/연결 보조 · 단위 원",'',
        '금융 전체 확인은 미완료이며 대출 승인·거절·등급 판단이 아닙니다.',
        f"목적: {md(view['purpose'])}",f"보고서 ID: {view['report_id']}",f"게시 내용 해시: {view['report_hash']}",'',
        '## 핵심 요약','']
    lines += ['- '+md(f['text']) for f in view['facts']]
    if not view['facts']:lines.append('모델 설명은 미작성/보류 상태입니다. 아래 검증 표와 미확인을 확인하세요.')
    lines += ['','## 해석','']+[md(x) for x in view['interpretations']]
    if not view['interpretations']:lines.append('별도의 검증된 해석 문장은 없습니다.')
    lines += ['','## 우선 질문','']
    for question in view['questions']:
        lines += ['- '+md(question['question']),'  - 요청 자료: '+md(question['requested_information']),
            '  - 이유: '+md(question['reason']),'  - 상태: '+('답변 수신·검증 전' if question.get('status')=='answered_unverified' else '미해결')]
        lines += ['  - 관찰 근거: '+md(b['text']) for b in question.get('observed_basis',[])]
        if question.get('answer_effect'):lines.append('  - 답변 후 확인: '+md(question['answer_effect']))
    lines += ['','## 일곱 항목의 확인 범위','']
    for item in view.get('minimum_review',[]):
        lines += ['### '+md(item['title']),'',md(item['observed']),'남은 부분: '+md(item['remaining']),'']
    for item in view.get('business_review',[]):
        lines += ['### '+md(item['title']),'',md(item['limitation']),'']
        if item.get('quote'):lines.append('> '+md(item['quote']['text'])+' ['+item['quote']['id']+']')
        else:lines.append('원문 인용 확인 보류')
    for finding in view.get('public_findings',[]):
        lines += ['### '+md(finding['title']),'',md(finding['limitation']),'']
        if finding['status']!='verified_excerpt':lines.append('원문 인용 확인 보류: 자료 부재·범위·다중 후보를 확인해야 합니다.')
        for quote in finding['quotes']:lines.append('> '+md(quote['text'])+' ['+quote['id']+']')
    for section in view['sections']:
        lines += ['### '+md(section['title']),'',md(section['limitation']),f"원문 검색 {section['search_count']}회. 전체 검토 완료가 아닙니다.",'']
        for item in section['fragments'][:2]:lines.append('> '+md(item['text']))
    lines += ['','## 검증 수치표','']
    for table in tables(view):
        lines += ['### '+md(table['title']),'','| 계정 (원) | '+' | '.join(table['ends'])+' |',
                  '| --- | '+' | '.join('---:' for _ in table['ends'])+' |']
        for row in table['rows']:
            lines.append('| '+md(row['label'])+' | '+' | '.join(md(c['display'])+
                (' ['+','.join(c['evidence_ids'][:1])+']' if c['evidence_ids'] else '') for c in row['cells'])+' |')
        lines.append('')
    lines += ['## 차트','']
    for chart in view['charts']:
        lines += ['### '+md(chart['topic'])+' ('+md(chart['period_kind'])+')','']
        if chart['status']=='withheld':lines += ['공식 버전 대응이 미확인이므로 비교 차트를 보류했습니다.','']
        else:lines += [f"![{md(chart['topic'])}](charts/{chart['id']}.svg)",'']
    lines += ['## 미확인','']+['- '+md(u['text']) for u in view['unknowns']]
    if view['user_observations']:
        lines += ['','## 사용자 제공 미검증 입력','']+['- '+md(o['text']) for o in view['user_observations']]
        for observation in view['user_observations']:
            for item in observation.get('attachments',[]):
                lines += ['- 첨부 '+md(item['name'])+' · '+md(item['status'])+' · 금융 미검증',
                    '  - 읽지 못한 페이지: '+md(str(item.get('unreadable_pages',[]))),
                    '  - 읽기 한계: '+md(' / '.join(item.get('warnings',[]))),
                    '  - 제한 발췌: '+md(item.get('text_preview',''))]
    lines += ['','## 한계','']+['- '+md(x) for x in view['limitations']]
    lines += ['','## 근거 목록','',
        '[전체 근거·원문 위치·추가 참조는 evidence.json](evidence.json)',
        '원문·사용자 첨부 전체는 이 ZIP에 포함하지 않습니다.','']
    for evidence in view['evidence']:
        link='[공식 원문]('+evidence['url']+')' if evidence['url'] else '공식 링크 없음'
        lines.append('- '+evidence['id']+' '+md(evidence['receipt_number'])+' · '+md(evidence['location'])+' · '+('게시 수치 확인' if evidence['use_status']=='verified' else '원문 인용 확인' if evidence['use_status']=='verified_excerpt' else '사용 보류·원문 기재')+' · '+link)
    return '\n'.join(lines)


def markdown_zip(view):
    files={'report.md':markdown(view).encode(),
        'evidence.json':safe_payload({'report_id':view['report_id'],'version':view['version'],
            'report_hash':view['report_hash'],'view_hash':view['view_hash'],'evidence':view['evidence']}).encode(),
        'manifest.json':safe_payload({'renderer':RENDERER,'report_id':view['report_id'],'version':view['version'],
            'report_hash':view['report_hash'],'view_hash':view['view_hash'],'financial_confirmation':'incomplete',
            'raw_sources_included':False,'user_attachments_included':False}).encode()}
    for chart in view['charts']:
        if chart['status']!='withheld':files['charts/'+chart['id']+'.svg']=chart_svg(view,chart['id']).encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,content in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,content)
    return output.getvalue()


def pdf(view):
    with FONT_LOCK:
        for name,file in [('FinanceKorean','NanumGothic-Regular.ttf'),('FinanceKoreanBold','NanumGothic-Bold.ttf')]:
            if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(ROOT/'static/fonts'/file)))
    regular=ParagraphStyle('FinanceBody',fontName='FinanceKorean',fontSize=9,leading=14,wordWrap='CJK',spaceAfter=6)
    small=ParagraphStyle('FinanceSmall',parent=regular,fontSize=7,leading=10,spaceAfter=3)
    heading=ParagraphStyle('FinanceHeading',parent=regular,fontName='FinanceKoreanBold',fontSize=14,leading=21,spaceBefore=12,spaceAfter=9)
    title=ParagraphStyle('FinanceTitle',parent=heading,fontSize=22,leading=30)
    def para(value,style=regular):return Paragraph(escape(text(value)).replace('\n','<br/>'),style)
    story=[para(view['company']['name']+' 운전자금 사전조사',title),
        para(f"{view['version']}판  |  공개 기준 {view['public_cutoff']}  |  별도 주·연결 보조"),
        para('금융 전체 확인 미완료 · 대출 승인/거절/등급 판단이 아닙니다.'),para('목적: '+view['purpose']),Spacer(1,12),para('핵심 요약',heading)]
    story += [para('• '+f['text']) for f in view['facts']]
    if not view['facts']:story.append(para('모델 설명은 미작성/보류입니다. 검증된 표와 미확인 범위를 제공합니다.'))
    story.append(para('해석',heading))
    story += [para(x) for x in view['interpretations']] or [para('별도의 검증된 해석 문장은 없습니다.')]
    story.append(para('우선 질문',heading))
    for i,q in enumerate(view['questions'],1):
        story.append(KeepTogether([para(str(i)+'. '+q['question']),para('요청 자료: '+q['requested_information'],small),
            para('이유: '+q['reason'],small)]+[para('관찰 근거: '+b['text'],small) for b in q.get('observed_basis',[])]+
            ([para('답변 후 확인: '+q['answer_effect'],small)] if q.get('answer_effect') else [])+
            [para('상태: '+('답변 수신·검증 전' if q.get('status')=='answered_unverified' else '미해결'),small),Spacer(1,6)]))
    story += [PageBreak(),para('조사 기준·일곱 항목의 확인 범위',heading)]
    for item in view.get('minimum_review',[]):
        story.append(KeepTogether([para(item['title'],heading),para(item['observed'],small),para('남은 부분: '+item['remaining'],small)]))
    for item in view.get('business_review',[]):
        block=[para(item['title'],heading),para(item['limitation'],small)]
        if item.get('quote'):block.append(para(item['quote']['text']+' ['+item['quote']['id']+']',small))
        else:block.append(para('원문 인용 확인 보류',small))
        story.append(KeepTogether(block))
    for finding in view.get('public_findings',[]):
        story += [para(finding['title'],heading),para(finding['limitation'],small)]
        if finding['status']!='verified_excerpt':story.append(para('원문 인용 확인 보류: 자료 부재·범위·다중 후보를 확인해야 합니다.'))
        for quote in finding['quotes']:story.append(para(quote['text']+' ['+quote['id']+']',small))
    for section in view['sections']:
        story += [para(section['title'],heading),para(section['limitation']),para(f"원문 검색 {section['search_count']}회 · 일부 확인",small)]
        for fragment in section['fragments'][:2]:story.append(para(fragment['text'],small))
    story += [PageBreak(),para('검증 수치표',heading),para('단위: 원. 미확인 값은 0으로 대체하지 않습니다. 반기 흐름은 동기간 누적, 잔액은 직전 연말과 반기 말입니다.')]
    for spec in tables(view):
        cells=[[para('계정 (원)',small)]+[para(end,small) for end in spec['ends']]]
        for row in spec['rows']:
            cells.append([para(row['label'],small)]+[para(c['display']+(' ['+c['evidence_ids'][0]+']' if c['evidence_ids'] else ''),small) for c in row['cells']])
        width=507;first=133;other=(width-first)/len(spec['ends'])
        table=Table(cells,colWidths=[first]+[other]*len(spec['ends']),repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9eff7')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#a3b4ca')),('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#dce2eb')),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        story += [para(spec['title'],heading),table,Spacer(1,12)]
    story += [PageBreak(),para('차트',heading)]
    from decimal import Decimal
    for chart in view['charts']:
        story.append(para(chart['topic']+' · '+chart['period_kind'],heading))
        if chart['status']=='withheld':story.append(para('공식 버전 대응이 미확인이므로 비교 차트를 보류했습니다.'));continue
        drawing=Drawing(500,210);baseline=125;points=chart['points'];scale=max((abs(Decimal(p['value'])) for p in points if p.get('status')=='verified'),default=Decimal(1)) or Decimal(1)
        drawing.add(Line(15,baseline,490,baseline,strokeColor=colors.HexColor('#a3b4ca')))
        step=470/max(1,len(points))
        for i,p in enumerate(points):
            x=20+i*step
            if p.get('status')=='verified':
                v=Decimal(p['value']);height=float(abs(v)/scale*65);y=baseline if v>=0 else baseline-height
                drawing.add(Rect(x,y,step*.55,height,fillColor=colors.HexColor('#2563eb' if p['series_index']==0 else '#059669'),strokeColor=None))
                drawing.add(String(x,baseline+height+6 if v>=0 else baseline-height-13,p['value'],fontName='FinanceKorean',fontSize=6))
            else:drawing.add(String(x,baseline+7,'미확인',fontName='FinanceKorean',fontSize=7))
            drawing.add(String(x,20,p['end'][:4],fontName='FinanceKorean',fontSize=7))
            drawing.add(String(x,8,p['label'],fontName='FinanceKorean',fontSize=6))
        story.append(KeepTogether([drawing,para('별도·원. 파랑은 첫 계열, 초록은 두 번째 계열이며 계정명과 값을 함께 표시합니다.',small)]))
    story += [para('미확인',heading)]+[para('• '+u['text']) for u in view['unknowns']]
    if view['user_observations']:
        story += [para('사용자 제공 미검증 입력',heading)]+[para(o['text']) for o in view['user_observations']]
        for observation in view['user_observations']:
            for item in observation.get('attachments',[]):
                story += [para('첨부 '+item['name']+' · '+item['status']+' · 금융 미검증',small),
                    para('읽지 못한 페이지: '+str(item.get('unreadable_pages',[])),small),
                    para('읽기 한계: '+' / '.join(item.get('warnings',[])),small),para(item.get('text_preview',''),small)]
    story += [para('한계',heading)]+[para('• '+x) for x in view['limitations']]
    story += [PageBreak(),para('근거·원문 위치 부록',heading),para('대표 위치를 수치표의 E번호와 연결했습니다. 전체 추가 참조와 콘텐츠 해시는 동일 버전 Markdown ZIP의 evidence.json에 있습니다.',small)]
    used={c['evidence_ids'][0] for table in tables(view) for row in table['rows'] for c in row['cells'] if c['evidence_ids']}
    used.update(q['id'] for f in view.get('public_findings',[]) for q in f['quotes'])
    used.update(i['quote']['id'] for i in view.get('business_review',[]) if i.get('quote'))
    for item in view['evidence']:
        if item['id'] not in used:continue
        label='게시 수치 확인' if item['use_status']=='verified' else '원문 인용 확인' if item['use_status']=='verified_excerpt' else '사용 보류·원문 기재'
        line=item['id']+' · '+str(item['receipt_number'])+' · '+('보증 공시' if item['scope']=='guarantee_disclosure' else scope_name(item['scope']))+' · '+item['unit']+' · '+item['location']+' · '+label
        block=[para(line,small),para('원문 표시: '+item['excerpt'],small)]
        if item['url']:block.append(Paragraph('<link href="'+escape(item['url'],quote=True)+'" color="#2563eb">공식 공시 원문 열기</link>',small))
        story.append(KeepTogether(block+[Spacer(1,5)]))
    story += [Spacer(1,10),para('보고서 ID: '+view['report_id'],small),para('게시 내용 해시: '+view['report_hash'],small),para('표시 데이터 해시: '+view['view_hash'],small)]
    output=io.BytesIO()
    def footer(c,doc):
        c.saveState();c.setFont('FinanceKorean',8);c.setFillColor(colors.HexColor('#526276'))
        c.drawString(44,24,view['company']['name']+' · '+str(view['version'])+'판 · 금융 미확인 유지')
        c.drawRightString(A4[0]-44,24,str(doc.page));c.restoreState()
    doc=SimpleDocTemplate(output,pagesize=A4,leftMargin=44,rightMargin=44,topMargin=42,bottomMargin=42,
        title=view['company']['name']+' 사전조사 '+str(view['version'])+'판',author='로컬 금융 사전조사',pageCompression=1)
    doc.build(story,onFirstPage=footer,onLaterPages=footer,canvasmaker=partial(canvas.Canvas,invariant=1))
    return output.getvalue()


class ReportExports:
    def __init__(self,store):self.store=store

    def create(self,report_id,kind):
        if kind not in {'pdf','markdown_zip'}:raise ValueError('지원하지 않는 출력 형식입니다.')
        view=build_view(self.store,report_id)
        export_id=digest(safe_payload({'renderer':RENDERER,'view_hash':view['view_hash'],'kind':kind}).encode())
        directory=self.store._safe_source_path('_exports/'+export_id);descriptor=directory/'manifest.json'
        with self.store._source_lock(export_id[:32],blocking=True):
            if descriptor.exists():return self.describe(export_id)
            data=pdf(view) if kind=='pdf' else markdown_zip(view)
            if len(data)>20*1024*1024:raise ValueError('출력 크기 한도를 초과했습니다.')
            name='report.pdf' if kind=='pdf' else 'report.zip'
            metadata={'id':export_id,'report_id':report_id,'version':view['version'],'format':kind,'file_name':name,
                'content_hash':digest(data),'report_hash':view['report_hash'],'view_hash':view['view_hash'],'renderer':RENDERER}
            directory.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.export-',dir=directory.parent) as temp:
                staging=Path(temp)/'ready';staging.mkdir();(staging/name).write_bytes(data);(staging/'manifest.json').write_text(safe_payload(metadata))
                try:os.rename(staging,directory)
                except FileExistsError:pass
            return self.describe(export_id)

    def describe(self,export_id):
        if not re.fullmatch('[0-9a-f]{64}',export_id):raise ValueError('없는 출력 ID입니다.')
        directory=self.store._safe_source_path('_exports/'+export_id)
        manifest=self.store._safe_source_path('_exports/'+export_id+'/manifest.json')
        metadata=json.loads(manifest.read_text())
        if metadata['id']!=export_id or metadata['file_name'] not in {'report.pdf','report.zip'}:raise IntegrityFailure('export_manifest_invalid')
        report=self.store.get_report(metadata['report_id'])
        if report['status']!='published' or report['publication']['content_hash']!=metadata['report_hash']:raise IntegrityFailure('export_report_mismatch')
        path=self.store._safe_source_path('_exports/'+export_id+'/'+metadata['file_name'])
        if digest(path.read_bytes())!=metadata['content_hash']:raise IntegrityFailure('export_content_changed')
        return {**metadata,'download_url':'/exports/'+export_id}

    def download(self,export_id):
        meta=self.describe(export_id)
        path=self.store._safe_source_path('_exports/'+export_id+'/'+meta['file_name'])
        return meta,path.read_bytes()
