"""Real HTTP/worker smoke using owned synthetic tasks and zero model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8001);args=parser.parse_args()
    from langchain_core.language_models.chat_models import BaseChatModel
    def forbidden(*a,**k): raise AssertionError('Model calls are disabled during activation smoke')
    BaseChatModel._generate_with_cache=forbidden
    BaseChatModel._agenerate_with_cache=forbidden
    from persistence.repository import PostgresLearningSessionRepository
    from persistence.db import get_sessionmaker
    from persistence.models import GradingRecordRow,LearningSessionRow
    from services.grading_gateway import save_standalone
    from tools.runtime_profiles import PROFILES
    from sqlalchemy import delete,select,func
    from worker.probe_browser import COUNTER,ALTERNATE
    owner='p28-activation-smoke-'+uuid.uuid4().hex
    repo=PostgresLearningSessionRepository()
    out=ROOT/'logs/p2-8-activation-20261030';out.mkdir(exist_ok=True)
    ledger=ROOT/'logs/p2-8-live-20261030/budget.json'
    before_ledger=ledger.read_bytes()
    records=[]
    def call(path,body=None,method=None):
        request=urllib.request.Request('http://127.0.0.1:'+str(args.port)+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'},method=method)
        with urllib.request.urlopen(request,timeout=180) as response: return json.load(response)
    with get_sessionmaker()() as db:
        sessions_before=db.scalar(select(func.count()).select_from(LearningSessionRow))
        assert not db.scalar(select(func.count()).select_from(GradingRecordRow).where(GradingRecordRow.user_id==owner))
    once={'actions':[{'type':'click','role':'button','name':'Increment'}],'queries':[{'selector':'output','read':'text'}]}
    twice={**once,'actions':once['actions']*2}
    cases=[('python-core','python','main.py','Read an integer and print twice its value.',
        'print(int(input())*2)', 'n=int(input());print(n+n)', ['print(int(input()))','print(42)'],
        [('21\n','42\n'),('0\n','0\n')], 'print(999)', '21\n','42\n'),
        ('react-dom','typescript','App.tsx','Export a default React counter with an accessible Increment button and output count initially0.',
        COUNTER,ALTERNATE,[COUNTER.replace('setN(n+1)','setN(0)'),COUNTER.replace('useState(0)','useState(99)')],
        [(json.dumps(once),'["1"]'),(json.dumps(twice),'["2"]')],COUNTER.replace('setN(n+1)','setN(0)'),json.dumps(once),'["1"]\n')]
    try:
        for profile,language,entry,statement,good,alternate,negatives,checks,bad,stdin,stdout in cases:
            artifact={'format':'coding_challenge','method':'execution','language':language,'execution_profile':profile,
                'profile_revision':PROFILES[profile].revision,'entrypoint':entry,'instructions':'Submit '+entry+'.',
                'io_checks':[{'id':'c'+str(i),'stdin':data,'expected_stdout':expected,'max_points':50,
                    'comparison_mode':'json' if profile=='react-dom' else 'text'} for i,(data,expected) in enumerate(checks)],
                'reference_files':{entry:good},'alternate_correct_files':[{entry:alternate}],
                'negative_control_files':[{entry:code} for code in negatives]}
            exercise=save_standalone(repo,owner,{'id':profile,'revision':'smoke-r1','origin':'operator-probe',
                'format':'coding_challenge','language':language,'statement':statement,'grading_artifact':artifact})
            assert exercise['status']=='ready','Reference/control preflight failed'
            base='/exercises/'+owner+'/'+exercise['id']
            run_body={'solution':good,'stdin':stdin,'exercise_revision':'smoke-r1','run_id':'run-'+profile}
            result=call(base+'/run',run_body)
            assert result['status']=='succeeded' and result.get('stdout')==stdout and 'score' not in result,result
            assert call(base+'/run',run_body)==result
            body={'solution':good,'exercise_revision':'smoke-r1','attempt_id':'good-'+profile}
            first=call(base+'/grade',body);replay=call(base+'/grade',body)
            assert first['grade_result']['score']==100 and replay['grade_result']['score']==100
            assert first['grade_result']['applied'] is False
            wrong=call(base+'/grade',{'solution':bad,'exercise_revision':'smoke-r1','attempt_id':'bad-'+profile})
            assert wrong['grade_result']['score']==0,wrong['grade_result']
            saved=call(base)
            assert saved['attempts']==2
            forbidden_fields={'grading_artifact','reference_files','expected_stdout','unit_tests','answer_key'}
            def keys(value):
                if isinstance(value,dict):
                    return set(value).union(*(keys(v) for v in value.values()))
                if isinstance(value,list): return set().union(*(keys(v) for v in value))
                return set()
            assert not keys(saved)&forbidden_fields
            try: call('/exercises/foreign-'+owner+'/'+exercise['id'])
            except urllib.error.HTTPError as error: assert error.code==404
            else: raise AssertionError('Foreign exercise was exposed')
            records.append({'profile':profile,'run':'passed','good_score':100,'bad_score':0,'replay':'passed','privacy':'passed'})
            print('Real HTTP smoke passed:',profile,flush=True)
    finally:
        assert owner.startswith('p28-activation-smoke-') and len(owner)>40
        with get_sessionmaker()() as db:
            db.execute(delete(GradingRecordRow).where(GradingRecordRow.user_id==owner));db.commit()
            assert db.scalar(select(func.count()).select_from(LearningSessionRow))==sessions_before
        assert ledger.read_bytes()==before_ledger,'The model budget changed during zero-model smoke'
        (out/'http-smoke.json').write_text(json.dumps({'records':records,'model_calls':0,'original_sessions_unchanged':True},indent=2))


if __name__=='__main__':main()
