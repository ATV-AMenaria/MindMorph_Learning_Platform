"""Operator helpers must fail closed when Windows cannot identify listeners."""
from types import SimpleNamespace

import pytest

from scripts import activate_p2_8 as activation


def test_failed_listener_query_cannot_be_treated_as_vacant(monkeypatch):
    monkeypatch.setattr(activation.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(RuntimeError, match='Cannot establish listener ownership'):
        activation.process_for_port(8000)


def test_successful_empty_listener_query_is_vacant(monkeypatch):
    monkeypatch.setattr(activation.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=''))
    assert activation.process_for_port(8000) == []


def test_unknown_listener_is_never_stopped(monkeypatch):
    monkeypatch.setattr(activation, 'process_for_port', lambda port: [
        {'ProcessId': 1234, 'ExecutablePath': None, 'CommandLine': None}])
    monkeypatch.setattr(activation.subprocess, 'run', lambda *a, **k: pytest.fail('Must not stop an unknown process'))
    with pytest.raises(RuntimeError, match='unrecognized service'):
        activation.stop_owned(8000, 'run_local_api.py')
