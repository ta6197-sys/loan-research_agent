"""첨부 묶음의 고정 저장·제한 파싱·후속 실행 연결. 외부 전송/원본 삭제 없음."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from agent.research_models import SourceInput
from agent.research_store import Conflict,IntegrityFailure,NotFound,digest,safe_payload,now
from .attachment_parser import MAX_FILE

MAX_FILES=10
MAX_TOTAL=100*1024*1024
TYPES={'.pdf':'application/pdf','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.csv':'text/csv','.txt':'text/plain'}


class AttachmentService:
    def __init__(self,store):self.store=store;self.root=store.source_root/'_attachments'

    def path(self,bundle):
        if not re.fullmatch('[0-9a-f]{64}',bundle):raise ValueError('invalid_attachment_bundle')
        if self.root.is_symlink() or (self.root/bundle).is_symlink():raise IntegrityFailure('attachment_symlink')
        return self.root/bundle

    def get(self,bundle,research_id=None,parent_report_id=None):
        folder=self.path(bundle)
        try:value=json.loads((folder/'manifest.json').read_text())
        except (OSError,ValueError):raise NotFound('첨부 묶음을 읽을 수 없습니다. 저장 원문과 입력은 보존됩니다.') from None
        if value['bundle_id']!=bundle or digest(safe_payload(value['identity']).encode())!=bundle:raise IntegrityFailure('attachment_manifest_changed')
        if ([{k:f[k] for k in ('name','hash','size')} for f in value['files']]!=value['identity']['files']
                or digest(safe_payload(value['files']).encode())!=value['parsed_hash']):raise IntegrityFailure('attachment_parser_record_changed')
        if research_id and value['identity']['research_id']!=research_id:raise Conflict('다른 조사의 첨부를 혼합하지 않습니다.')
        if parent_report_id and value['identity']['parent_report_id']!=parent_report_id:raise Conflict('첨부의 기준 게시본이 다릅니다.')
        for index,item in enumerate(value['files']):
            file=folder/(str(index)+TYPES_SUFFIX(item['name']))
            if file.is_symlink() or file.stat().st_size>MAX_FILE or digest(file.read_bytes())!=item['hash']:raise IntegrityFailure('attachment_content_changed')
        return value

    def stage(self,research_id,parent_report_id,files):
        parent=self.store.get_report(parent_report_id)
        if parent['research_id']!=research_id or parent['status']!='published':raise Conflict('같은 조사의 게시본에 첨부해주세요.')
        if not 1<=len(files)<=MAX_FILES or sum(len(raw) for _,raw in files)>MAX_TOTAL:raise ValueError('attachment_total_limit')
        descriptors=[]
        for name,raw in files:
            if (not name or len(name)>200 or Path(name).name!=name or '\\' in name or any(ord(c)<32 for c in name)
                    or Path(name).suffix.lower() not in TYPES):raise ValueError('unsupported_attachment_name_or_type')
            if len(raw)>MAX_FILE:raise ValueError('attachment_file_limit')
            for key,secret in os.environ.items():
                if re.search(r'key|token|secret|password',key,re.I) and len(secret)>=8 and secret.encode() in raw:
                    raise ValueError('known_secret_in_attachment')
            descriptors.append({'name':name,'hash':digest(raw),'size':len(raw)})
        identity={'research_id':research_id,'parent_report_id':parent_report_id,'files':descriptors}
        bundle=digest(safe_payload(identity).encode());destination=self.path(bundle);self.store._private_directory(self.root)
        with self.store._source_lock(digest(('attachments:'+research_id).encode())[:32],blocking=True):
            if destination.exists():return self.public(self.get(bundle,research_id,parent_report_id))
            existing=[]
            for folder in self.root.iterdir():
                if not folder.is_dir() or not re.fullmatch('[0-9a-f]{64}',folder.name):continue
                value=self.get(folder.name)
                if value['identity']['research_id']==research_id:existing+=value['files']
            if len(existing)+len(files)>MAX_FILES or sum(f['size'] for f in existing)+sum(f['size'] for f in descriptors)>MAX_TOTAL:
                raise ValueError('attachment_research_limit')
            with tempfile.TemporaryDirectory(prefix='attachment-',dir=self.root) as temp:
                temp=Path(temp);self.store._private_directory(temp);parsed=[]
                for index,((name,raw),item) in enumerate(zip(files,descriptors)):
                    file=temp/(str(index)+TYPES_SUFFIX(name));file.write_bytes(raw);self.store._private_file(file)
                    project=Path(__file__).resolve().parents[2]
                    env={'PATH':os.defpath,'PYTHONPATH':str(project),'PYTHONIOENCODING':'utf-8',
                        'LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false'}
                    try:
                        run=subprocess.run([sys.executable,'-m','agent.finance.attachment_worker',str(file),name],
                            cwd=temp,env=env,capture_output=True,timeout=20)
                        if run.returncode or len(run.stdout)>4*1024*1024:raise ValueError('parser_failed')
                        result=json.loads(run.stdout)
                    except (subprocess.TimeoutExpired,ValueError):
                        result={'status':'unreadable','reason':'parser_failed_or_timed_out','complete_read':False,
                            'financial_verification':'not_performed','text':'','locations':[]}
                    if result['status']=='limit_exceeded':raise ValueError('attachment_'+result['reason'])
                    parsed.append({**item,'parsed':result})
                manifest={'bundle_id':bundle,'identity':identity,'files':parsed,'parsed_hash':digest(safe_payload(parsed).encode()),'received_at':now()}
                manifest_path=temp/'manifest.json';manifest_path.write_text(safe_payload(manifest));self.store._private_file(manifest_path)
                os.rename(temp,destination);self.store._private_directory(destination)
                # TemporaryDirectory 종료 시 이미 옮긴 경로만 비어 있으므로 원본을 지우지 않는다.
            with self.store.connection(write=True) as conn:
                _,basis=self.store._context(conn,parent['run_id'])
                self.store._event(conn,research_id,basis['id'],None,'attachment_bundle_stored',
                    {'bundle_id':bundle,'file_count':len(parsed),'local_only':True,'financial_verification':'not_performed'})
        return self.public(manifest)

    @staticmethod
    def public(value):
        return {'bundle_id':value['bundle_id'],'files':[{'name':f['name'],'hash':f['hash'],'size':f['size'],
            'status':f['parsed']['status'],'complete_read':f['parsed'].get('complete_read',False),
            'warnings':f['parsed'].get('warnings',[]),'unreadable_pages':f['parsed'].get('unreadable_pages',[]),
            'missing_formula_cache_count':f['parsed'].get('missing_formula_cache_count',0)} for f in value['files']],
            'financial_verification':'not_performed','external_requests':0}

    def attach_to_run(self,bundle,research_id,parent_report_id,run_id,input_id):
        value=self.get(bundle,research_id,parent_report_id)
        with self.store._source_lock(digest(('attachment-input:'+input_id).encode())[:32],blocking=True):
            for index,item in enumerate(value['files']):
                with self.store.connection() as conn:
                    rows=conn.execute("SELECT id FROM research_sources WHERE run_id=? AND json_extract(metadata_json,'$.reporting_period.attachment_for_input_id')=? AND json_extract(metadata_json,'$.reporting_period.attachment_index')=?",(run_id,input_id,index)).fetchall()
                if len(rows)>1:raise IntegrityFailure('duplicate_attachment_source')
                if rows:
                    if digest(self.store.source_content(rows[0]['id']))!=item['hash']:raise IntegrityFailure('attachment_source_changed')
                    continue
                raw=(self.path(bundle)/(str(index)+TYPES_SUFFIX(item['name']))).read_bytes()
                self.store.save_source(run_id,SourceInput(kind='user',title=item['name'],publisher='사용자 제공 첨부',
                    retrieved_at=value['received_at'],media_type=TYPES[TYPES_SUFFIX(item['name'])],rights_status='user_authorized',
                    reporting_period={'operation':'followup_attachment','attachment_for_input_id':input_id,
                        'attachment_index':index,'attachment_bundle_id':bundle,'read_status':item['parsed']['status']},
                    usage_restrictions=['user_provided_unverified','local_only','no_formula_or_external_link_execution']),raw)

    def observations(self,bundle,input_id=None):
        value=self.get(bundle)
        result=[{'name':item['name'],'hash':item['hash'],'status':item['parsed']['status'],'complete_read':item['parsed'].get('complete_read',False),
            'text_preview':item['parsed'].get('text','')[:500],'warnings':item['parsed'].get('warnings',[]),
            'locations_preview':[{k:v[:200] if k=='text' else v for k,v in location.items()} for location in item['parsed'].get('locations',[])[:10]],
            'unreadable_pages':item['parsed'].get('unreadable_pages',[]),'financial_verification':'not_performed'} for item in value['files']]
        if input_id:
            for index,item in enumerate(result):
                with self.store.connection() as conn:
                    rows=conn.execute("SELECT id FROM research_sources WHERE json_extract(metadata_json,'$.reporting_period.attachment_for_input_id')=? AND json_extract(metadata_json,'$.reporting_period.attachment_index')=?",(input_id,index)).fetchall()
                if len(rows)!=1 or digest(self.store.source_content(rows[0]['id']))!=item['hash']:raise IntegrityFailure('attachment_reference_missing_or_changed')
                item['source_id']=rows[0]['id']
        return result


def TYPES_SUFFIX(name):return Path(name).suffix.lower()
