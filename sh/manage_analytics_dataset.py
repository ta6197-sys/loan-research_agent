"""명시적 코호트 등록·생성·연결·수집. 조회 API는 이 명령을 실행하지 않는다."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.research_store import ResearchStore
from agent.finance.cohort_registry import CohortRegistry
from agent.finance.analytics_dataset_service import AnalyticsDatasetService
from agent.finance.general_collection import GeneralCollectionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='session.db')
    parser.add_argument('--sources', default='data/research')
    sub = parser.add_subparsers(dest='action', required=True)
    register = sub.add_parser('register'); register.add_argument('policy_file')
    build = sub.add_parser('build'); build.add_argument('cohort_id'); build.add_argument('report_ids', nargs='+')
    bind = sub.add_parser('bind'); bind.add_argument('report_id'); bind.add_argument('artifact_id')
    collect = sub.add_parser('collect'); collect.add_argument('run_ids', nargs='+')
    args = parser.parse_args(); store = ResearchStore(args.db, args.sources)
    if args.action == 'register': result = CohortRegistry(store).register(json.loads(Path(args.policy_file).read_text()))
    elif args.action == 'build': result = AnalyticsDatasetService(store).build(args.cohort_id, args.report_ids)
    elif args.action == 'bind': AnalyticsDatasetService(store).bind(args.report_id, args.artifact_id); result = {'status': 'bound'}
    else:
        # 별도 일반 조사 접수로 확보한 run_id만 허용한다. 호출 예산·재시도 이력은 기존 서비스가 보존한다.
        async def collect_runs():
            outcomes = []
            for run_id in dict.fromkeys(args.run_ids):
                run = store.get_run(run_id)
                if run['status'] == 'finished':
                    outcomes.append({'run_id': run_id, 'status': 'already_finished_no_collection'})
                    continue
                try: outcomes.append({'run_id': run_id, 'result': await GeneralCollectionService(store).run(run_id)})
                except Exception as error: outcomes.append({'run_id': run_id, 'status': 'failed', 'error_type': type(error).__name__})
            return outcomes
        result = asyncio.run(collect_runs())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
