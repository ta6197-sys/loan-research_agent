"""Use the remaining approved local probe budget on archived verified rows.

Only the selected, already verified public rows are sent to loopback Ollama.
No report, stored validation batch or original source is changed.
"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.local_model import LocalFinanceModel
from agent.research_api import _run_verified_analysis_payload
from agent.research_store import ResearchStore, safe_payload

DATA=ROOT/'data/research/broad_303_20261027'
COUNTER=ROOT/'data/research/validation_local_model/round_20261027/probe_session.json'
RUN_ID='c80a9e1af83f445aa7d246ef11c9e87d'


async def main():
    store=ResearchStore(DATA/'session.db',DATA/'sources')
    analysis=_run_verified_analysis_payload(store,RUN_ID)
    if (analysis['xbrl_gate']['status']!='pass' or
            analysis['disclosure_history']['status']!='pass'):
        raise ValueError('archived_financial_gates_not_passed')
    verified=[row for row in analysis['financial_rows']
              if row['status']=='verified' and row['scope']=='OFS'
              and row['account'] in ('revenue','operating_profit')]
    verified=sorted(verified,key=lambda row:(row['end'],row['account']))[-2:]
    if len(verified)!=2:
        raise ValueError('verified_model_evidence_missing')
    withheld=next((row for row in analysis['financial_rows']
                   if row['status']=='withheld' and row['scope']=='OFS'),None)
    if withheld is None:
        raise ValueError('withheld_model_evidence_missing')
    evidence=[{'evidence_id':'E'+str(i),'state':'verified',
               'text':f"현대공업 별도재무제표 {row['end']} {row['label']}은 {row['value']}원이다."}
              for i,row in enumerate(verified,1)]
    evidence.append({'evidence_id':'W1','state':'withheld',
                     'text':f"현대공업 별도재무제표 {withheld['end']} {withheld['label']} 수치의 검증은 보류됐다."})
    calls=0
    async def reserve():
        nonlocal calls
        state=json.loads(COUNTER.read_text())
        if state['round_id']!='20261027' or state['model_calls']>=5 or calls>=2:
            raise ValueError('probe_budget_exhausted')
        state['model_calls']+=1;calls+=1
        COUNTER.write_text(safe_payload(state))
    result=None;errors=[];feedback=None
    for attempt in range(2):
        try:
            result=await LocalFinanceModel().draft(evidence,before_request=reserve,
                                                  validation_feedback=feedback)
            break
        except Exception as error:
            reason=str(error)
            errors.append({'attempt':attempt+1,'error_type':type(error).__name__,
                           'reason':reason[:120]})
            if reason not in {'withheld_evidence_not_preserved','unsupported_or_modified_fact',
                              'invalid_followup_question','duplicate_fact_reference',
                              'followup_question_missing'}:
                break
            feedback=reason
    report={'schema_version':'local-finance-company-probe-1.0','corp_code':'00164502',
            'run_id':RUN_ID,'model':'qwen3.5:2b','accepted':result is not None,
            'model_calls':calls,'external_model_calls':0,
            'input_validation_ids':sorted({v for row in verified for v in row['validation_ids']}),
            'input_source_ids':sorted({v for row in verified for v in row['source_ids']}),
            'evidence_sha256':sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'verified_fact_count':len(result['draft']['facts']) if result else 0,
            'withheld_preserved':result is not None and result['draft']['unknown_evidence_ids']==['W1'],
            'question_count':len(result['draft']['questions']) if result else 0,
            'errors':errors,'report_created':False,
            'limitations':'단일 기업의 보관 검증 수치에 대한 로컬 초안 계약 검사다. 금융 정확성이나 신용 판단을 보증하지 않는다.'}
    print(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    return 0 if report['accepted'] else 1


if __name__=='__main__':sys.exit(asyncio.run(main()))
