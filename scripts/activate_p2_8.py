"""Preserve the existing local memory app, validate PostgreSQL, then cut over.

Never stops an unrecognized process, drops a table/volume, or overwrites a
database session modified outside this operator's preceding import.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'logs/p2-8-activation-20261030'
OUT.mkdir(parents=True, exist_ok=True)


def http(path, port=8000):
    with urllib.request.urlopen('http://127.0.0.1:' + str(port) + path, timeout=10) as response:
        return json.load(response)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def export_memory():
    if http('/health').get('store') != 'memory': raise RuntimeError('Source app is no longer the expected memory service')
    path = ROOT / 'logs/refresh-2026-10-28/runtime-checkpoint.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if (datetime.now(timezone.utc) - datetime.fromisoformat(data['saved_at'])).total_seconds() > 45:
        raise RuntimeError('The live emergency checkpoint is stale; source app remains running')
    users = {row['user_id'] for row in data['sessions']}
    previous = {(row['user_id'], row['session_id']): row for row in data['sessions']}
    rows = []
    for user in users:
        for meta in http('/sessions/' + urllib.parse.quote(user, safe='')):
            sid = meta['session_id']
            response = http('/sessions/' + urllib.parse.quote(user, safe='') + '/' + urllib.parse.quote(sid, safe=''))
            rows.append({'user_id': user, 'session_id': sid, 'title': meta['title'],
                'revision': previous.get((user,sid), {}).get('revision', 1), 'learning_session': response['learning_session']})
    data['sessions'] = rows
    data['exported_at'] = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = OUT / ('memory-source-' + stamp + '.json')
    backup.write_text(json.dumps(data, indent=2), encoding='utf-8')
    (OUT / 'latest-memory-source.json').write_bytes(backup.read_bytes())
    print('Backed up live memory state:', len(rows), 'sessions;', sum(map(len, data.get('rag',{}).values())), 'document chunks.', flush=True)
    return data


def critical(ls):
    return {key: ls.get(key) for key in ['skill_graph','node_state','assessment','chat','summary','preferences','learner_profile']} | {
        'lessons': {node: {'content': lesson.get('content'), 'statement': (lesson.get('exercise') or {}).get('statement')}
            for node,lesson in ls.get('lessons',{}).items()}}


def import_memory(data):
    from sqlalchemy import select
    from persistence.db import get_sessionmaker, check_ready
    from persistence.models import LearningSessionRow, RagChunkRow
    from persistence.repository import PostgresLearningSessionRepository
    from services.grading_gateway import synchronize_session
    from rag.embeddings import EMBED_DIM
    check_ready()
    plan_path = OUT / 'import-plan.json'
    prior = json.loads(plan_path.read_text()) if plan_path.exists() else {'sessions': {}}
    plan = {'sessions': {}, 'mode': 'prepared'}
    prepared = []
    repo = PostgresLearningSessionRepository()
    for row in data['sessions']:
        ls = json.loads(json.dumps(row['learning_session']))
        for node,lesson in ls.get('lessons',{}).items():
            exercise = lesson.get('exercise')
            if not isinstance(exercise,dict): continue
            identity = sha([row['user_id'],row['session_id'],node,exercise.get('statement')])
            exercise.setdefault('id', uuid.uuid5(uuid.NAMESPACE_URL, identity).hex)
            exercise.setdefault('revision', sha([exercise.get('statement'),exercise.get('grading_artifact')])[:32])
        synchronize_session(repo, row['user_id'], row['session_id'], ls)
        if critical(ls) != critical(row['learning_session']): raise RuntimeError('Teaching/progress changed during preparation')
        key = sha([row['user_id'],row['session_id']])
        plan['sessions'][key] = {'sha256': sha(ls)}
        prepared.append((row,ls,key))
    with get_sessionmaker()() as db:
        for row,ls,key in prepared:
            stored = db.execute(select(LearningSessionRow).where(LearningSessionRow.user_id==row['user_id'],
                LearningSessionRow.session_id==row['session_id']).with_for_update()).scalar_one_or_none()
            if stored is None:
                db.add(LearningSessionRow(user_id=row['user_id'],session_id=row['session_id'],title=row['title'],
                    revision=max(1,int(row.get('revision',1))),data=ls))
            elif sha(stored.data) != sha(ls):
                if prior.get('mode') != 'prepared' or prior['sessions'].get(key,{}).get('sha256') != sha(stored.data):
                    raise RuntimeError('Existing durable progress differs; refusing an unreviewed overwrite')
                stored.data=ls; stored.revision+=1; stored.title=row['title']
        for user, chunks in data.get('rag',{}).items():
            existing = list(db.execute(select(RagChunkRow).where(RagChunkRow.user_id==user)).scalars())
            signatures = {(r.source_id,r.page,r.source,r.content) for r in existing}
            for item in chunks.values():
                meta = item.get('metadata',{})
                vector = [float(v) for v in item['vector']]
                if len(vector)!=EMBED_DIM or not all(math.isfinite(v) for v in vector): raise RuntimeError('Invalid saved document vector')
                signature = (meta.get('source_id'),meta.get('page'),meta.get('source','upload'),item['text'])
                if signature not in signatures:
                    db.add(RagChunkRow(user_id=user,source_id=signature[0],page=signature[1],source=signature[2],content=signature[3],embedding=vector))
                    signatures.add(signature)
        db.commit()
    for row,ls,_ in prepared:
        saved = repo.get(row['user_id'],row['session_id'])
        if sha(saved)!=sha(ls): raise RuntimeError('Durable session verification failed')
    plan['source_exported_at'] = data['exported_at']
    plan_path.write_text(json.dumps(plan,indent=2))
    print('Verified durable import: teaching, tasks, mastery, diagnostics, chat and document vectors preserved.',flush=True)


def process_for_port(port):
    command = "$ErrorActionPreference='Stop'; Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq " + str(port) + " } | ForEach-Object { Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess) -ErrorAction Stop } | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,timeout=30)
    if result.returncode != 0:
        raise RuntimeError('Cannot establish listener ownership; refusing to treat the port as vacant')
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows,dict): rows=[rows]
    return rows


def stop_owned(port, marker):
    for row in process_for_port(port):
        command = row.get('CommandLine') or ''
        if str(ROOT).lower() not in command.lower() or marker not in command or 'mindmorph' not in (row.get('ExecutablePath') or '').lower():
            raise RuntimeError('Refusing to stop an unrecognized service')
        if 'run_local_api.py' in command:
            (OUT / ('stop-' + str(int(row['ProcessId'])) + '.request')).write_text('Operator requested graceful shutdown')
            for _ in range(80):
                if not process_for_port(port): return
                time.sleep(.2)
            raise RuntimeError('Owned API did not shut down by its graceful deadline')
        subprocess.run(['powershell','-NoProfile','-Command','Stop-Process -Id ' + str(int(row['ProcessId'])) + ' -ErrorAction Stop'],check=True,timeout=30)


def start(port):
    existing = process_for_port(port)
    if existing:
        if not all(str(ROOT).lower() in (r.get('CommandLine') or '').lower() and 'run_local_api.py' in (r.get('CommandLine') or '') for r in existing):
            raise RuntimeError('Requested API port belongs to another service')
        return
    env={**os.environ,'MINDMORPH_STORE':'postgres'}
    with (OUT / ('backend-' + str(port) + '.log')).open('a',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts/run_local_api.py'),'--port',str(port)],cwd=ROOT,
            env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
    (OUT/('backend-'+str(port)+'.pid')).write_text(str(process.pid))
    for _ in range(60):
        if process.poll() is not None: raise RuntimeError('New API failed to start; see its private operator log')
        try:
            if http('/ready',port).get('durable') is True: return
        except Exception: pass
        time.sleep(1)
    raise RuntimeError('New API readiness deadline exceeded')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cutover',action='store_true')
    args=parser.parse_args()
    data=export_memory()
    import_memory(data)
    start(8001)
    assert http('/ready',8001)['durable']
    if not args.cutover:
        print('Preflight API is ready on127.0.0.1:8001; current app remains on8000.',flush=True)
        return
    # Re-export immediately before changing the listener; import refuses external DB changes.
    data=export_memory(); import_memory(data)
    stop_owned(8000,'logs\\refresh-2026-10-28\\start_backend.py')
    from dotenv import set_key
    set_key(str(ROOT/'.env'),'MINDMORPH_STORE','postgres',quote_mode='never')
    start(8000)
    if http('/ready').get('durable') is not True: raise RuntimeError('Activated API is not durable')
    for row in data['sessions']:
        response=http('/sessions/'+urllib.parse.quote(row['user_id'],safe='')+'/'+urllib.parse.quote(row['session_id'],safe=''))
        if response['learning_session']['node_state'] != row['learning_session']['node_state']:
            # last_feedback is an intentional public allowlist projection.
            from services.public_projection import public_session
            if response['learning_session']['node_state'] != public_session(row['learning_session'])['node_state']:
                raise RuntimeError('Activated progress verification failed')
    stop_owned(8001,'scripts\\run_local_api.py')
    plan=json.loads((OUT/'import-plan.json').read_text());plan['mode']='active';plan['activated_at']=datetime.now(timezone.utc).isoformat()
    (OUT/'import-plan.json').write_text(json.dumps(plan,indent=2))
    print('Activated API on127.0.0.1:8000 with PostgreSQL; all preserved paths verified.',flush=True)


if __name__=='__main__': main()
