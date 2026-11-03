"""Acceptance must distinguish actual wrong-code grading and complete review."""
import pytest

from scripts.evaluate_p2_8 import negative_solution, require_formative, completed_evidence


def test_nonempty_negative_is_selected_after_empty_control():
    artifact = {'entrypoint': 'main.js', 'negative_control_files': [
        {'main.js': ''}, {'main.js': 'console.log(999)'}]}
    assert negative_solution(artifact) == 'console.log(999)'
    artifact['negative_control_files'].pop()
    with pytest.raises(ValueError, match='nonempty'):
        negative_solution(artifact)


def test_platform_failure_is_not_accepted_as_formative_feedback():
    with pytest.raises(ValueError, match='complete formative'):
        require_formative({'status': 'platform_error', 'score': None, 'mastery_eligible': False})
    require_formative({'status': 'feedback_only', 'score': None, 'mastery_eligible': False,
                       'criteria': [{'criterion_id': 'c1'}]})


def test_old_complete_flag_cannot_hide_a_failed_review():
    record = {'complete': True, 'tutor': 'next step', 'reloaded': {'saved': True},
              'synthetic_response': 'answer', 'feedback': {'status': 'platform_error', 'score': None}}
    assert not completed_evidence(record)
    record['feedback'] = {'status': 'feedback_only', 'score': None, 'mastery_eligible': False,
                          'criteria': [{'criterion_id': 'c1'}]}
    assert completed_evidence(record)
