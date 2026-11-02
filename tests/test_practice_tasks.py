"""New practice is explicit, versioned and cannot erase saved teaching/progress."""
import pytest

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore
from persistence.repository import InMemoryLearningSessionRepository
from services.mastery import default_node_state
from services.practice_tasks import replace_task, PracticeUnavailableError


@pytest.fixture
def repo():
    repo = InMemoryLearningSessionRepository()
    repo.save('u', 's', {'skill_graph': {'nodes': [{'id': 'a', 'label': 'A'}], 'edges': []},
        'node_state': {'a': default_node_state()}, 'events': [], 'chat': [], 'lessons': {'a': {
            'content': 'Keep the saved teaching', 'exercise': {'id': 'old', 'revision': 'r0',
                'statement': 'Saved practice', 'format': 'coding_challenge', 'language': 'json'}}}})
    return repo


def fresh():
    return {'id': 'new', 'revision': 'r1', 'origin': 'generated', 'format': 'coding_challenge',
        'language': 'json', 'statement': 'Reply with n equal to42', 'grading_artifact': {
            'method': 'structured', 'checks': [{'id': 'n', 'answer_key': 'n', 'expected': 42, 'max_points': 100}]}}


def test_new_task_preserves_teaching_progress_and_retries_without_regeneration(repo, monkeypatch):
    calls = []
    def generate(*args):
        calls.append(1)
        newer = repo.get('u', 's'); newer['chat'].append({'role': 'user', 'content': 'Concurrent note'}); repo.save('u', 's', newer)
        return fresh()
    monkeypatch.setattr('services.learning_service.new_practice', generate)
    before = repo.get('u', 's')['node_state']
    result = replace_task(repo, 'u', 's', 'a', 'r0', 'once')
    assert result['lessons']['a']['content'] == 'Keep the saved teaching'
    assert result['node_state'] == before and result['chat'][0]['content'] == 'Concurrent note'
    assert result['lessons']['a']['exercise']['revision'] == 'r1'
    assert 'grading_artifact' not in result['lessons']['a']['exercise']
    replace_task(repo, 'u', 's', 'a', 'r0', 'once')
    assert calls == [1]
    with pytest.raises(SessionConflictError): replace_task(repo, 'u', 's', 'a', 'different', 'once')


def test_failed_release_never_replaces_old_task_and_retry_keeps_budget(repo, monkeypatch):
    calls = []
    def generate(*args):
        calls.append(1)
        return {**fresh(), 'grading_artifact': {'unit_tests': ['pass']}}
    monkeypatch.setattr('services.learning_service.new_practice', generate)
    for _ in range(2):
        with pytest.raises(PracticeUnavailableError): replace_task(repo, 'u', 's', 'a', 'r0', 'bad')
    assert calls == [1] and repo.get('u', 's')['lessons']['a']['exercise']['id'] == 'old'


def test_crash_after_replacement_recovers_without_changing_newer_state(repo, monkeypatch):
    calls = []
    monkeypatch.setattr('services.learning_service.new_practice', lambda *args: calls.append(1) or fresh())
    update = GradingStore.update
    def fail(self, user, key, changes, **kwargs):
        if changes.get('state') == 'completed': raise OSError('Interrupted')
        return update(self, user, key, changes, **kwargs)
    monkeypatch.setattr(GradingStore, 'update', fail)
    with pytest.raises(OSError): replace_task(repo, 'u', 's', 'a', 'r0', 'recover')
    current = repo.get('u', 's'); current['chat'] = [{'role': 'user', 'content': 'Newer work'}]; repo.save('u', 's', current)
    monkeypatch.setattr(GradingStore, 'update', update)
    result = replace_task(repo, 'u', 's', 'a', 'r0', 'recover')
    assert calls == [1] and result['chat'][0]['content'] == 'Newer work'
