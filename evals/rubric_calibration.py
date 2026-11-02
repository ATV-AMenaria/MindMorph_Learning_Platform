"""Independent-label rubric benchmark. Missing provenance never enables mastery."""
import hashlib
import json
from pathlib import Path

CATEGORIES = ('code_review', 'analytical_design', 'written_practice')


def validate_labels(data):
    provenance = data.get('provenance', {})
    if (provenance.get('kind') not in {'human','expert'} or not provenance.get('labeler')
            or not provenance.get('review_reference') or provenance.get('not_model_generated') is not True):
        raise ValueError('Independent human/expert label provenance is required; model-generated labels are not accepted.')
    samples = data.get('samples', [])
    if len({s.get('id') for s in samples}) != len(samples): raise ValueError('Calibration IDs must be unique.')
    for category in CATEGORIES:
        rows = [s for s in samples if s.get('category') == category]
        if len(rows) < 20 or any(s.get('held_out') is not True for s in rows):
            raise ValueError('Each category needs at least20 held-out independent labels.')
        if len([s for s in rows if s.get('boundary')]) < 5: raise ValueError('Each category needs five boundary/ambiguity cases.')
        if not any(s.get('critical') and s.get('expected_band') != 'mastered' for s in rows):
            raise ValueError('Each category needs critical wrong-answer/injection controls.')
        if len({s.get('language') for s in rows}) < 2 or len({s.get('verbosity') for s in rows}) < 2:
            raise ValueError('Multilingual and verbosity variants are required.')
        if any(s.get('expected_band') not in {'mastered','in_progress','remediation'} for s in rows):
            raise ValueError('Independent decision labels must use the agreed80/40 bands.')
    return samples


def evaluate_labels(data, judge):
    samples = validate_labels(data)
    report = {'active_mode': 'formative_only', 'categories': {}}
    for category in CATEGORIES:
        rows = [s for s in samples if s['category'] == category]
        outcomes = []
        for sample in rows:
            decisions = []
            for _ in range(3 if sample.get('boundary') else 1):
                verdict = judge(sample['submission'], sample['artifact'])
                if verdict.get('status') != 'feedback_only' or verdict.get('score') is not None or verdict.get('mastery_eligible'):
                    decisions.append('unvalidated'); continue
                criteria = verdict.get('criteria') or []
                if not criteria: decisions.append('unvalidated'); continue
                score = sum(c['earned_points'] for c in criteria)
                decisions.append('mastered' if score >= 80 else 'remediation' if score < 40 else 'in_progress')
            outcomes.append({'id': sample['id'], 'expected': sample['expected_band'], 'decisions': decisions,
                'critical': sample.get('critical',False)})
        valid = all('unvalidated' not in o['decisions'] for o in outcomes)
        agree = sum((o['decisions'][0]=='mastered')==(o['expected']=='mastered') for o in outcomes) / len(outcomes)
        false_mastery = sum(o['critical'] and o['expected']!='mastered' and 'mastered' in o['decisions'] for o in outcomes)
        stable = all(len(set(o['decisions'])) == 1 for o in outcomes)
        report['categories'][category] = {'benchmark_passed': valid and agree>=.95 and false_mastery==0 and stable,
            'mastery_agreement': agree, 'critical_false_mastery': false_mastery, 'boundary_stable': stable,
            'label_count': len(rows), 'outcomes': outcomes}
    # A report is review evidence, never a self-issued runtime eligibility flag.
    report['benchmark_passed'] = all(c['benchmark_passed'] for c in report['categories'].values())
    return report


def current_status():
    return {'active_mode': 'formative_only', 'numeric_mastery_categories': [], 'independent_label_count': 0,
        'benchmark_evaluated': False, 'reason': 'No independently human/expert-labelled rubric dataset is present. All review remains formative, as required by the closure plan.',
        'required': {'held_out_per_category':20,'mastery_agreement':.95,'critical_false_mastery':0,'boundary_cases':5,'boundary_repeats':3}}
