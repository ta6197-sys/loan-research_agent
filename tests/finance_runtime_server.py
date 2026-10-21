"""강제 종료 시험 전용 서버. 실제 공시는 재검증하고 모델만 합성 응답으로 대체한다."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TARGET=Path(sys.argv[1]).resolve();GATE=sys.argv[2];PORT=int(sys.argv[3])
from agent.finance.local_model import LocalFinanceModel,checked_draft
from agent.finance.execution_journal import ExecutionJournal


def ready(label):
    (TARGET/'gate-ready').write_text(label)


async def synthetic_draft(self,evidence,*,before_request,**kwargs):
    await before_request()
    with (TARGET/'synthetic-model-calls.jsonl').open('a') as out:out.write(json.dumps({'synthetic':True})+'\n')
    if GATE=='model':ready('model_reserved');await asyncio.Future()
    gaps=[e['evidence_id'] for e in evidence if e['state']=='withheld']
    draft={'facts':[{'evidence_id':e['evidence_id'],'text':e['text']} for e in evidence if e['state']=='verified'],
           'unknown_evidence_ids':gaps,'questions':[{'question':'추가 확인 근거를 확보할 수 있습니까?','evidence_ids':gaps}]}
    return {'draft':checked_draft(draft,evidence),'usage':{'model_calls':1,'model':'synthetic-lifecycle-test'}}


original_account=ExecutionJournal.account

def gated_account(self,stage,**kwargs):
    if GATE=='recovery' and stage=='research_saved':
        ready('before_recovery_checkpoint')
        while True:time.sleep(.05)
    result=original_account(self,stage,**kwargs)
    if GATE=='written' and kwargs.get('phase')=='written':
        ready('draft_saved')
        while True:time.sleep(.05)
    return result


LocalFinanceModel.draft=synthetic_draft
ExecutionJournal.account=gated_account
import uvicorn
uvicorn.run('server:app',host='127.0.0.1',port=PORT,log_level='warning')
