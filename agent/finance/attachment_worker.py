"""부모 프로세스가 제한한 고정 파일 한 개만 읽는다. 환경 키·네트워크 사용 없음."""
import json
from pathlib import Path
import sys


def main():
    import resource
    try:resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    except (ValueError,OSError):pass
    try:resource.setrlimit(resource.RLIMIT_DATA if sys.platform=='darwin' else resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
    except (ValueError,OSError):pass
    from .attachment_parser import parse_attachment,AttachmentLimit
    try:
        result=parse_attachment(sys.argv[2],Path(sys.argv[1]).read_bytes())
    except AttachmentLimit as error:result={'status':'limit_exceeded','reason':str(error),'financial_verification':'not_performed','complete_read':False,'text':'','locations':[]}
    except Exception:result={'status':'unreadable','reason':'unsupported_or_damaged_file','financial_verification':'not_performed','complete_read':False,'text':'','locations':[]}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
