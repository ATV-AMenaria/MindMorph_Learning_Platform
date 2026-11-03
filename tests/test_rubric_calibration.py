"""Policy unit checks with fake judges; these are not expert calibration evidence."""
import pytest

from evals.rubric_calibration import CATEGORIES, validate_labels, evaluate_labels, current_status


def dataset():
    rows=[]
    for category in CATEGORIES:
        for index in range(20):
            rows.append({'id':category+str(index),'category':category,'held_out':True,'boundary':index<5,
                'critical':index==5,'expected_band':'mastered' if index>=10 else 'remediation',
                'language':'en' if index%2 else 'hi','verbosity':'short' if index%2 else 'long',
                'submission':str(index),'artifact':{'category':category}})
    return {'provenance':{'kind':'human','labeler':'unit-test-fixture-only','review_reference':'synthetic-test-data',
        'not_model_generated':True},'samples':rows}


def judge(text, artifact):
    return {'status':'feedback_only','score':None,'mastery_eligible':False,
        'criteria':[{'earned_points':100 if int(text)>=10 else 0}]}


def test_model_generated_labels_are_rejected_before_judging():
    data=dataset();data['provenance']['kind']='model'
    with pytest.raises(ValueError,match='Independent'): evaluate_labels(data,lambda *args:pytest.fail('Must not call a judge'))
    assert current_status()['numeric_mastery_categories']==[]


def test_critical_false_mastery_fails_even_at95_percent_agreement():
    def unsafe(text,artifact):
        result=judge(text,artifact)
        if text=='5': result['criteria'][0]['earned_points']=100
        return result
    report=evaluate_labels(dataset(),unsafe)
    assert not report['benchmark_passed']
    assert all(row['mastery_agreement']==.95 and row['critical_false_mastery']==1 for row in report['categories'].values())
    assert report['active_mode']=='formative_only'


def test_boundary_inconsistency_prevents_calibration_release():
    calls={}
    def unstable(text,artifact):
        key=artifact['category']+text;calls[key]=calls.get(key,0)+1
        result=judge(text,artifact)
        if text=='0' and calls[key]==2: result['criteria'][0]['earned_points']=100
        return result
    report=evaluate_labels(dataset(),unstable)
    assert not report['benchmark_passed'] and not any(row['boundary_stable'] for row in report['categories'].values())


def test_full_label_coverage_is_required_and_report_does_not_enable_runtime_mastery():
    data=dataset();assert len(validate_labels(data))==60
    report=evaluate_labels(data,judge)
    assert report['benchmark_passed'] and report['active_mode']=='formative_only'
    data['samples']=data['samples'][:-1]
    with pytest.raises(ValueError,match='at least20'): validate_labels(data)
