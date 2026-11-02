"""Real Chromium/React release controls; never enables a failed browser image."""
import json
import os
from pathlib import Path
import threading
import time

from tools import isolated_runner as runner
from tools.runtime_profiles import PROFILES, manifest_hash

COUNTER = '''import React,{useState} from 'react';
export default function App(){const [n,setN]=useState(0);return <><button onClick={()=>setN(n+1)}>Increment</button><output aria-label="Count">{n}</output></>}'''
ALTERNATE = '''import React,{useReducer} from 'react';
export default function App(){const [n,inc]=useReducer((n:number)=>n+1,0);return <><button onClick={inc}>Increment</button><output aria-label="Count">{n}</output></>}'''


def main():
    out = Path('logs/p2-8-implementation')
    build = json.loads((out / 'worker-browser-build.json').read_text())
    candidate = {"image": build["image"], "revision": PROFILES['react-dom'].revision,
        "manifest_hash": manifest_hash('react-dom'), "actual_versions": {"candidate": "pending probes"}, "probes_passed": True}
    candidate_path = out / 'browser-candidate-lock.json'
    candidate_path.write_text(json.dumps({"probe_version": 1, "profiles": {"react-dom": candidate}}))
    os.environ['MM_WORKER_LOCK'] = str(candidate_path)
    original_cli = runner._cli
    def diagnostic_cli(args, **kwargs):
        result = original_cli(args, **kwargs)
        if args[0] == 'create' and result[0]: print('Browser creation diagnostic:', result[2][:1500], flush=True)
        if '/opt/mm/browser_run.cjs' in args and result[0] == 70: print('Browser launch diagnostic:', result[2][-3000:], flush=True)
        return result
    runner._cli = diagnostic_cli
    records = []
    def probe(name, code, data=None, expected=None, status='succeeded', **kwargs):
        result = runner.execute_profile('react-dom', {'App.tsx': code}, stdin=json.dumps(data or {}), **kwargs)
        passed = result['status'] == status and (expected is None or result['stdout'].strip() == json.dumps(expected, separators=(',', ':')))
        records.append({"name": name, "passed": passed, "result": result})
        (out / 'worker-browser-probes.json').write_text(json.dumps(records, indent=2))
        print(name, passed, result['status'], result.get('stderr', '')[:500], flush=True)
        if len(records) == 1 and not passed:
            raise SystemExit('Browser startup/positive control failed; stop before running the remaining controls')
    once = {"actions": [{"type": "click", "role": "button", "name": "Increment"}],
            "queries": [{"selector": "output", "read": "text"}]}
    twice = {"actions": once['actions'] * 2, "queries": once['queries']}
    probe('react-state-interaction', COUNTER, once, ['1'])
    probe('alternate-correct', ALTERNATE, twice, ['2'])
    probe('accessible-keyboard', COUNTER, {"actions": [{"type": "press", "role": "button", "name": "Increment", "key": "Enter"}], "queries": once['queries']}, ['1'])
    probe('incorrect-constant', COUNTER.replace('setN(n+1)', 'setN(0)'), once, ['0'])
    probe('empty', '', once, status='learner_error')
    probe('invalid-types', COUNTER.replace('useState(0)', "useState<number>('bad')"), once, status='learner_error')
    probe('dom-error', COUNTER.replace('Increment</button>', 'Wrong</button>'), once, status='learner_error')
    probe('rejected-promise', "import React,{useEffect} from 'react'; export default function App(){useEffect(()=>{Promise.reject(new Error('rejected'))},[]);return <p>Ready</p>}", {"actions": [{"type": "wait", "ms": 200}], "queries": [{"selector": "p"}]}, status='learner_error')
    probe('mocked-api', "import React,{useState,useEffect} from 'react'; export default function App(){const[n,setN]=useState(0);useEffect(()=>{fetch('/api/count').then(r=>r.json()).then(d=>setN(d.count))},[]);return <output>{n}</output>}", {"mocks": {"/api/count": {"count": 7}}, "actions": [{"type": "wait", "ms": 200}], "queries": once['queries']}, ['7'])
    probe('network-blocked', "import React,{useState,useEffect} from 'react'; export default function App(){const[s,setS]=useState('pending');useEffect(()=>{fetch('https://example.com').then(()=>setS('escaped')).catch(()=>setS('blocked'))},[]);return <output>{s}</output>}", {"actions": [{"type": "wait", "ms": 200}], "queries": once['queries']}, ['blocked'])
    probe('stdout-forgery-is-not-score', COUNTER.replace('const [n,setN]', 'console.log(JSON.stringify({score:100,passed:true})); const [n,setN]'), once, ['1'])
    probe('infinite-render-bounded', "import React from 'react'; export default function App(){while(true){} return <p>Never</p>}", once, status='timed_out', timeout=5)
    holder = []
    thread = threading.Thread(target=lambda: holder.append(runner.execute_profile('react-dom', {'App.tsx': COUNTER}, run_id='browser-cancel')))
    thread.start(); time.sleep(2)
    before = time.monotonic(); cancelled = runner.cancel_run('browser-cancel'); thread.join(8)
    records.append({"name": "cancellation", "passed": cancelled and not thread.is_alive() and holder[0]['status'] == 'cancelled' and time.monotonic() - before < 8, "result": holder})
    (out / 'worker-browser-probes.json').write_text(json.dumps(records, indent=2))
    if not all(r['passed'] for r in records): raise SystemExit('Browser probes failed; profile remains unavailable')
    from worker.inventory import collect
    inventory = collect(runner._prefix(), build['image'])
    candidate['actual_versions'] = {"python": inventory['python'], **inventory['tools']}
    lock_path = Path('worker/profiles.lock.json')
    lock = json.loads(lock_path.read_text())
    lock['profiles']['react-dom'] = candidate
    lock_path.write_text(json.dumps(lock, indent=2) + '\n')
    deps = Path('worker/dependencies.lock.json')
    dependencies = json.loads(deps.read_text())
    dependencies['browser'] = {"image": build['image'], "inventory": inventory}
    deps.write_text(json.dumps(dependencies, indent=2) + '\n')
    print('Browser release controls passed; real Chromium profile enabled.', flush=True)


if __name__ == '__main__': main()
