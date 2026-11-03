"""Failed review stages remain ungraded and never expose private exception text."""
from unittest.mock import Mock

import pytest

from agents.exercise import grader_agent
from agents.exercise.grading_schema import RubricScore, CriterionVerdict


@pytest.mark.parametrize('stage', ['provider', 'criteria', 'evidence'])
def test_review_failure_has_safe_stage_and_no_numeric_grade(monkeypatch, stage):
    model = Mock()
    invoke = model.with_structured_output.return_value.invoke
    if stage == 'provider':
        invoke.side_effect = ValueError('PRIVATE_EXCEPTION_CONTENT')
    else:
        invoke.return_value = RubricScore(criteria=[CriterionVerdict(
            criterion_id='wrong-id' if stage == 'criteria' else 'c1',
            earned_points=100, evidence=['PRIVATE_INVENTED_EVIDENCE'])], feedback='draft')
    monkeypatch.setattr(grader_agent, 'llm', model)
    result = grader_agent._grade_case_study('My actual answer', {
        'exercise_statement': 'Explain your choice.',
        'rubric': [{'criterion_id': 'c1', 'criterion': 'Justification', 'weight': 100}]})
    assert result['status'] == 'platform_error' and result['score'] is None
    assert result['mastery_eligible'] is False
    assert result['error_code'] == f'rubric_{stage}_error'
    assert 'PRIVATE_' not in str(result)
