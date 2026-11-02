"""Explicit live acceptance on synthetic learners, under the shared persisted budget."""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.live_budget import LiveBudget, EvaluationStopped

OUT = ROOT / 'logs/p2-8-live-20261030'
OUT.mkdir(parents=True, exist_ok=True)
EVIDENCE_VERSION = 2


def negative_solution(artifact):
    """Exercise a real wrong submission, independently of the empty-input gate."""
    entry = artifact['entrypoint']
    for files in artifact.get('negative_control_files', []):
        value = files.get(entry, '')
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError('No nonempty negative learner submission is available')


def require_formative(result):
    if (not isinstance(result, dict) or result.get('status') != 'feedback_only'
            or not result.get('criteria') or result.get('mastery_eligible')
            or result.get('score') is not None):
        raise ValueError('A complete formative review without numeric mastery was required')


def completed_evidence(record):
    if not record.get('complete') or not record.get('tutor') or not record.get('reloaded'):
        return False
    if record.get('synthetic_response'):
        try:
            require_formative(record.get('feedback'))
        except ValueError:
            return False
        return True
    good, bad = record.get('correct_result') or {}, record.get('incorrect_result') or {}
    return (good.get('status') == 'graded' and good.get('score') == 100
            and bad.get('status') == 'graded' and isinstance(bad.get('score'), (int, float))
            and 0 <= bad['score'] < 80)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--only', default='')
    parser.add_argument('--retry-after-throttle', action='store_true')
    parser.add_argument('--availability', action='store_true')
    parser.add_argument('--refresh-failed', action='store_true')
    parser.add_argument('--targeted', action='store_true')
    parser.add_argument('--review-attempt', default='written-v3',
                        help='Stable explicit review attempt identity; change only for an authorized terminal-failure retry.')
    parser.add_argument('--token-cap', type=int, default=2_000_000,
                        help='Previously authorized cumulative token ceiling; must match the saved ledger.')
    args = parser.parse_args()
    os.environ.update(KAFKA_BOOTSTRAP_SERVERS='', LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false',
                      MINDMORPH_NEO4J_MIRROR='0')
    budget = LiveBudget(OUT / 'budget.json', tokens=args.token_cap, paced=True, max_output=4096)
    if args.retry_after_throttle: budget.allow_retry_after_cooldown()
    import config
    if args.availability:
        import urllib.request
        print('Configured Groq key count:', len(config.groq_api_keys()), 'model:', config.GROQ_MODEL)
        try:
            url = getattr(config, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            data = json.load(urllib.request.urlopen(url.rstrip('/') + '/api/tags', timeout=3))
            print('Local model names:', [model.get('name') for model in data.get('models', [])])
        except Exception as error: print('Local availability:', type(error).__name__)
        return
    with budget.install():
        if args.probe:
            started = time.monotonic()
            response = config.llm.invoke('Reply only with OK.')
            report = {'available': bool(response.content), 'seconds': round(time.monotonic()-started, 2), 'budget': budget.totals()}
            (OUT / 'provider-probe.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return
        if args.targeted: run_targeted(budget, args.only)
        else: run_matrix(budget, args.only, args.refresh_failed, args.review_attempt)


def run_matrix(budget, only, refresh_failed=False, review_attempt='written-v3'):
    from services import learning_service as service
    from services import grading_gateway as gateway
    from persistence.repository import InMemoryLearningSessionRepository
    from services.completion import locked_node_ids
    from services.public_projection import public_session
    cases = [
        ('python-preference', 'Create a learning roadmap for processing a short list of daily expenses, using Python.', 'python', 'foundation', 'beginner', 'Software'),
        ('javascript-preference', 'Create a learning roadmap for processing a short list of daily expenses, using JavaScript.', 'javascript', 'foundation', 'beginner', 'Software'),
        ('sql-beginner', 'Create a learning roadmap for grouping and summarizing sales records with SQL.', 'sql', 'foundation', 'beginner', 'Data'),
        ('sql-experienced', 'Create a learning roadmap for grouping and summarizing sales records with SQL.', 'sql', 'foundation', 'experienced', 'Data'),
        ('javascript-focused', 'Create a focused learning roadmap for JavaScript array filtering and mapping.', 'javascript', 'focused', 'beginner', 'Software'),
        ('javascript-foundation', 'Create a foundational learning roadmap for JavaScript array filtering and mapping.', 'javascript', 'foundation', 'beginner', 'Software'),
        ('guitar-beginner', 'Create a learning roadmap for guitar chord transitions and a simple practice routine.', None, 'foundation', 'beginner', 'Music'),
        ('guitar-experienced', 'Create a learning roadmap for guitar chord transitions and a simple practice routine.', None, 'foundation', 'experienced', 'Music'),
    ]
    results_path = OUT / 'journeys.json'
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    for name, goal, language, scope, experience, domain in cases:
        if only and name != only: continue
        if completed_evidence(results.get(name, {})) and results[name].get('evidence_version') == EVIDENCE_VERSION: continue
        old = results.get(name, {})
        previous_record = old
        if old and old.get('evidence_version') != EVIDENCE_VERSION:
            archive = OUT / 'superseded'
            archive.mkdir(exist_ok=True)
            (archive / (name + '-' + str(time.time_ns()) + '.json')).write_text(json.dumps(old, indent=2), encoding='utf-8')
            old = {}
        state_path = OUT / 'states' / (name + '.json')
        cached = json.loads(state_path.read_text()) if state_path.exists() else {}
        if cached.get('evidence_version') != EVIDENCE_VERSION: cached = {}
        user, repo = 'p28-live-' + uuid.uuid4().hex, InMemoryLearningSessionRepository()
        user = cached.get('user', user)
        ls = cached.get('session')
        sid = cached.get('sid')
        if ls:
            repo.save(user, sid, ls, title=goal)
            ls = repo.get(user, sid)
            repo._grading_records = {(user, key): value for key, value in cached.get('records', [])}
        record = {**old, 'name': name, 'goal': goal, 'language': language, 'scope': scope, 'experience': experience, 'complete': False, 'evidence_version': EVIDENCE_VERSION}
        needs_refresh = (refresh_failed and bool(record.get('error')) and not record.get('refinement')
                         and record.get('error') != 'Execution controls did not separate correct and incorrect work')
        if needs_refresh:
            archive = OUT / 'superseded'; archive.mkdir(exist_ok=True)
            (archive / (name + '-before-refinement-' + str(time.time_ns()) + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
        record.pop('blocked', None); record.pop('error', None); record.pop('error_type', None)
        def checkpoint():
            if ls is not None:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps({'evidence_version': EVIDENCE_VERSION, 'user': user, 'sid': sid,
                    'session': ls, 'records': [[key, value] for (uid, key), value in getattr(repo, '_grading_records', {}).items() if uid == user]}, indent=2), encoding='utf-8')
            results[name] = record
            results_path.write_text(json.dumps(results, indent=2), encoding='utf-8')
        started = time.monotonic()
        print('Live journey:', name, flush=True)
        try:
            if 'followup' not in record:
                record['followup'] = service.generate_followup(goal)
                checkpoint()
            preferences = {'experience': experience, 'scope': scope, 'time_commitment': '30 minutes daily', 'learning_style': 'worked examples and practice'}
            if language: preferences['language'] = language
            if ls is None:
                generated = service.start_session(goal, 'B', preferences=preferences, primary_language=language,
                    scope=scope, domain=domain, user_id=user)
                ls = generated.get('learning_session')
            if not ls: raise ValueError('Roadmap route did not return a learning session')
            sid = ls.get('graph_identity') or uuid.uuid4().hex
            record.setdefault('initial_session', public_session(ls))
            checkpoint()
            if ls.get('assessment') and not ls['assessment'].get('submitted'):
                questions = ls['assessment'].get('quiz', {}).get('questions', [])
                if name in {'javascript-preference', 'sql-experienced'} and all(type(q.get('correct_index')) is int for q in questions):
                    service.grade_assessment(ls, [q['correct_index'] for q in questions])
                    record['diagnostic_action'] = 'submitted'
                else:
                    service.grade_assessment(ls, action='skip')
                    record['diagnostic_action'] = 'skipped'
            unlocked = [n for n in ls['skill_graph']['nodes'] if n['id'] not in locked_node_ids(ls['skill_graph'], ls['node_state'])]
            if not unlocked: raise ValueError('No reachable starting skill')
            # Prefer an actual coding/practice objective when a graph also has abstract setup nodes.
            node = next((n for n in ls['skill_graph']['nodes'] if n['id'] == record.get('node_id')), None) or next((n for n in unlocked if any(word in (n.get('label','')+' '+n.get('description','')).lower() for word in ['array','list','query','select','chord','filter'])), unlocked[0])
            record['node_id'] = node['id']
            gateway.hydrate_session(repo,user,ls)
            with gateway.repair_context(repo,user,sid):
                ls = service.open_lesson(ls, node['id'], user_id=user)
            checkpoint()
            gateway.synchronize_session(repo, user, sid, ls)
            repo.save(user, sid, ls, title=goal)
            if needs_refresh:
                from services.practice_tasks import replace_task
                old_revision = ls['lessons'][node['id']]['exercise'].get('revision')
                ls = replace_task(repo, user, sid, node['id'], old_revision, 'schema-refinement-v4-' + name)
                record['refinement'] = 'Explicit new practice after the saved task failed evaluation; teaching and progress preserved.'
                record.pop('synthetic_response', None)
                record.pop('feedback', None)
                checkpoint()
            exercise = ls['lessons'][node['id']].get('exercise') or {}
            record['lesson'] = public_session(ls)['lessons'][node['id']]
            if not exercise.get('statement'): raise ValueError('No usable practice task')
            if exercise.get('format') != 'coding_challenge':
                for line in exercise['statement'].splitlines():
                    if re.search(r'(submit|upload|deliverable|file name|one.page).{0,100}(\bPDF\b|\.pdf|image file|audio file|video file|\.png|\.jpg)', line, re.I) and not re.search(r'optional|not required|no upload|do not', line, re.I):
                        raise ValueError('The task requires an attachment that the response surface cannot accept')
            checkpoint()
            store = __import__('persistence.grading_store', fromlist=['GradingStore']).GradingStore(repo)
            package = store.get(user, 'package:' + exercise.get('package_id','')) or {}
            artifact = package.get('artifact') or {}
            if artifact.get('method') == 'execution' and exercise.get('status') == 'ready':
                solution = artifact['reference_files'][artifact['entrypoint']]
                good, _ = gateway.accept_and_evaluate(repo, user, exercise, solution, exercise['revision'], 'good', context={'session_id': sid, 'node_id': node['id']})
                wrong = negative_solution(artifact)
                bad, _ = gateway.accept_and_evaluate(repo, user, exercise, wrong, exercise['revision'], 'bad-' + gateway.digest(wrong)[:16], context={'session_id': sid, 'node_id': node['id']})
                record['correct_result'], record['incorrect_result'] = good.get('result'), bad.get('result')
                if record['correct_result'].get('score') != 100 or record['incorrect_result'].get('score') is None or record['incorrect_result']['score'] >= 80:
                    raise ValueError('Execution controls did not separate correct and incorrect work')
                # Apply exactly once through the actual service, without intentional wrong-answer adaptation.
                ls, _, record['feedback'] = gateway.reconcile_lesson(repo, user, sid, node['id'], 'good')
            elif artifact.get('method') == 'rubric' or exercise.get('format') == 'case_study':
                import config
                response = record.get('synthetic_response') or config.llm.invoke('As a synthetic learner, write a concise plain-text response to this task. '
                    'Distinguish a proposed plan from performed actions. Do not claim uploaded files or observed physical performance. '
                    'This is an evaluation sample, not an independent expert-labelled calibration answer. TASK:\n' + exercise['statement']).content
                record['synthetic_response'] = response
                checkpoint()
                ls, _, record['feedback'] = gateway.grade_lesson(repo, user, sid, node['id'], response, exercise['revision'], review_attempt)
                require_formative(record['feedback'])
            else:
                raise ValueError('Generated exercise did not have an available assessment route')
            checkpoint()
            question = 'Explain one common mistake in this lesson and a small next practice step for my experience and goal.'
            messages = service.build_tutor_messages(ls, node['id'], question, user)
            async def tutor():
                return ''.join([chunk async for chunk in service._get_tutor_agent().astream(messages)])
            record['tutor'] = asyncio.run(tutor())
            if not record['tutor'].strip(): raise ValueError('Tutor returned no response')
            record['reloaded'] = public_session(repo.get(user, sid))
            record['complete'] = True
        except EvaluationStopped as error:
            record['blocked'] = str(error)
            checkpoint()
            raise
        except Exception as error:
            record['error_type'] = type(error).__name__
            record['error'] = str(error)[:500]
        record['seconds'] = round(time.monotonic()-started, 2)
        results[name] = record
        checkpoint()
        print('Live result:', name, record['complete'], record.get('error',''), flush=True)
    print(json.dumps(budget.totals()), flush=True)


def run_targeted(budget, only=''):
    from services.learning_service import new_practice
    from services import grading_gateway as gateway
    from services.learner_profile import make_profile
    from services.mastery import default_node_state
    from persistence.repository import InMemoryLearningSessionRepository
    from persistence.grading_store import GradingStore
    import config
    cases = [
        ('data-science','python','Data Science','Fit a tiny CPU scikit-learn linear regression from local numeric inputs and predict one value; no downloads.'),
        ('typescript-browser','typescript','Software','Build a React counter component with an accessible Increment button and visible count using the trusted browser template.'),
        ('compiled-cpp','cpp','Software','Read a short list of integers from stdin in C++ and print their sum.'),
        ('postgresql','postgresql','Data','Use PostgreSQL SQL to create local sales data and aggregate sales by category; preserve the PostgreSQL dialect.'),
        ('written-analysis',None,'Business','Write a plain-text comparison of two inventory-ordering options, with assumptions, risks and a justified choice.'),
        ('unsupported-haskell','haskell','Software','Write and explain a Haskell function to sum a list; native execution is unavailable so provide formative code review.'),
        ('finance-python','python','Finance','Use Python and NumPy to calculate simple returns from a short list of financial prices, with local inputs only.'),
        ('music-python','python','Music','Use Python math to calculate note frequencies from semitone offsets; this is explicitly coding practice in a music domain.'),
    ]
    path = OUT/'targeted.json'; results=json.loads(path.read_text()) if path.exists() else {}
    for name,language,domain,goal in cases:
        if only and name != only: continue
        if results.get(name,{}).get('complete'): continue
        if results.get(name):
            archive=OUT/'superseded';archive.mkdir(exist_ok=True)
            (archive/(name+'-targeted-'+str(time.time_ns())+'.json')).write_text(json.dumps(results[name],indent=2),encoding='utf-8')
        print('Targeted live check:',name,flush=True)
        repo,user=InMemoryLearningSessionRepository(),'p28-target-'+uuid.uuid4().hex
        profile=make_profile({'experience':'beginner'},language,'focused',domain,goal)
        ls={'skill_graph':{'nodes':[{'id':'a','label':goal,'description':goal}],'edges':[]},
            'node_state':{'a':default_node_state()},'lessons':{'a':{'content':'Practice the stated goal using small self-contained examples. '+goal}},
            'learner_profile':profile,'primary_language':language,'public_query':goal,'events':[]}
        result={'goal':goal,'language':language,'domain':domain,'complete':False}
        exercise=None
        try:
            exercise=new_practice(ls,'a',user)
            gateway.publish_exercise(repo,user,exercise,context={'standalone':True})
            result['exercise']=__import__('services.public_projection',fromlist=['public_exercise']).public_exercise(exercise)
            package=GradingStore(repo).get(user,'package:'+exercise['package_id']);artifact=package['artifact']
            if artifact.get('method')=='execution' and exercise.get('status')=='ready':
                good,_=gateway.accept_and_evaluate(repo,user,exercise,artifact['reference_files'][artifact['entrypoint']],exercise['revision'],'good',context={'standalone':True})
                wrong=negative_solution(artifact)
                bad,_=gateway.accept_and_evaluate(repo,user,exercise,wrong,exercise['revision'],'bad-'+gateway.digest(wrong)[:16],context={'standalone':True})
                result.update(correct_result=good.get('result'),incorrect_result=bad.get('result'))
                if result['correct_result'].get('score')!=100 or result['incorrect_result'].get('score') is None or result['incorrect_result']['score']>=80: raise ValueError('Execution evidence did not separate controls')
                if name=='postgresql' and artifact.get('execution_profile')!='postgresql': raise ValueError('SQL dialect was substituted')
                if name=='typescript-browser' and artifact.get('execution_profile')!='react-dom': raise ValueError('Browser task used a non-browser profile')
            elif artifact.get('method')=='rubric':
                answer=config.llm.invoke('Write a concise synthetic plain-text response to this practice task. Do not claim execution or physical observation. TASK:'+exercise['statement']).content
                attempt,_=gateway.accept_and_evaluate(repo,user,exercise,answer,exercise['revision'],'review',context={'standalone':True})
                result['review']=attempt.get('result')
                require_formative(result['review'])
                if name=='unsupported-haskell' and exercise.get('language')!='haskell': raise ValueError('Unsupported language identity was lost')
            else: raise ValueError('No available assessment route')
            result['complete']=True
        except EvaluationStopped as error:
            result['blocked']=str(error);results[name]=result;path.write_text(json.dumps(results,indent=2),encoding='utf-8');raise
        except Exception as error: result['error_type']=type(error).__name__;result['error']=str(error)[:500]
        finally:
            private=OUT/'targeted-states';private.mkdir(exist_ok=True)
            (private/(name+'-'+str(time.time_ns())+'.json')).write_text(json.dumps({
                'user':user,'exercise':exercise,'records':[[key,value] for (uid,key),value in getattr(repo,'_grading_records',{}).items() if uid==user]
            },indent=2),encoding='utf-8')
        results[name]=result;path.write_text(json.dumps(results,indent=2),encoding='utf-8')
        print('Targeted result:',name,result['complete'],result.get('error',''),flush=True)


if __name__ == '__main__':
    try: main()
    except EvaluationStopped as error:
        print('LIVE EVALUATION STOPPED:', str(error), flush=True)
        raise SystemExit(2)
