"""Explicit, replay-safe new practice for a saved lesson; old work stays intact."""
import uuid

from persistence.errors import SessionConflictError
from persistence.grading_store import GradingStore, clone
from services.grading_gateway import digest, publish_exercise


class PracticeUnavailableError(ValueError):
    pass


def replace_task(repo, user, sid, node, revision, request_id):
    from services.learning_service import new_practice, LockedNodeError
    from services.completion import locked_node_ids, incomplete_prereq_labels
    ls = repo.get(user, sid)
    if ls is None: raise KeyError('Session not found')
    if node in locked_node_ids(ls['skill_graph'], ls['node_state']):
        raise LockedNodeError(node, incomplete_prereq_labels(ls['skill_graph'], ls['node_state'], node))
    previous = (ls.get('lessons', {}).get(node) or {}).get('exercise') or {}
    if not previous: raise ValueError('Open an existing exercise first.')
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128: raise ValueError('A stable request ID is required.')
    store, key = GradingStore(repo), 'practice:' + request_id
    binding = digest({'session': sid, 'node': node, 'revision': revision})
    existing = store.get(user, key)
    if existing is None and previous.get('revision') != revision:
        raise SessionConflictError('This exercise changed. Reload before requesting another task.')
    from services import knowledge_tracking as tracking
    accepted = {'state': 'accepted', 'binding': binding}
    if existing is None and tracking.enabled():
        summary = tracking.summarize(store.knowledge_observations(user, sid, node))
        accepted['practice_guidance'] = summary['recommendation']
    record, _ = store.put_once(user, key, 'creation', accepted)
    if record['binding'] != binding: raise SessionConflictError('This request ID belongs to a different exercise.')
    if record.get('state') == 'completed': return ls
    if record.get('state') != 'generated':
        if previous.get('revision') != revision: raise SessionConflictError('This exercise changed. Reload first.')
        record, claimed = store.claim_generation(user, key)
        if not claimed: raise SessionConflictError('A new task is already being prepared. Retry the same request shortly.')
        token = record['lease_token']
        try:
            frozen = tracking.guidance(record.get('practice_guidance'))
            exercise = new_practice(ls, node, user, practice_guidance=frozen) if frozen else new_practice(ls, node, user)
            exercise.setdefault('id', uuid.uuid4().hex)
            exercise.setdefault('revision', uuid.uuid4().hex)
            record = store.update(user, key, {'state': 'generated', 'exercise': exercise}, expected_status='accepted', expected_token=token)
        except Exception:
            store.update(user, key, {'state': 'retryable_error'}, expected_status='accepted', expected_token=token)
            raise
        if record.get('state') != 'generated': raise SessionConflictError('Another worker recovered this request. Retry it.')
    ls = repo.get(user, sid)
    current = ls['lessons'][node]['exercise']
    exercise = clone(record['exercise'])
    if current.get('id') != exercise['id']:
        if current.get('revision') != revision:
            raise SessionConflictError('A newer practice task is already saved; it was not replaced.')
        publish_exercise(repo, user, exercise, context={'session_id': sid, 'node_id': node})
        if exercise.get('status') not in {'ready', 'feedback_only'}:
            raise PracticeUnavailableError('The new task did not pass assessment validation. Your saved exercise and draft are unchanged. Retry availability or request a different task.')
        ls['lessons'][node]['exercise'] = exercise
        if tracking.enabled():
            ls['node_state'][node]['knowledge_tracking'] = tracking.summarize(store.knowledge_observations(user, sid, node))
        repo.save(user, sid, ls)
    store.update(user, key, {'state': 'completed', 'exercise_id': exercise['id']}, expected_status='generated')
    return ls
