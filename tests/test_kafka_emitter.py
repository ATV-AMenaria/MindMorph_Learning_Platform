"""Tests for Kafka event emitter and LangSmith telemetry streaming."""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from services.events import STAGES, record_event
from services.kafka_emitter import emit_event_to_kafka, is_kafka_configured


def test_kafka_disabled_by_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KAFKA_BOOTSTRAP_SERVERS", None)
        assert is_kafka_configured() is False
        res = emit_event_to_kafka({"stage": STAGES.LESSON_OPENED, "node_id": "test_node"})
        assert res["status"] == "skipped"
        assert res["event_stage"] == STAGES.LESSON_OPENED


def test_kafka_emits_when_configured():
    with patch.dict(os.environ, {"KAFKA_BOOTSTRAP_SERVERS": "localhost:9092", "MINDMORPH_KAFKA_TOPIC": "test.topic"}):
        mock_producer = MagicMock()
        with patch("services.kafka_emitter.get_kafka_producer", return_value=mock_producer):
            res = emit_event_to_kafka({"stage": STAGES.EXERCISE_GRADED, "session_id": "sess_123", "score": 95.0})
            assert res["status"] == "emitted"
            assert res["topic"] == "test.topic"
            assert res["event_stage"] == STAGES.EXERCISE_GRADED
            mock_producer.send.assert_called_once()
            call_args = mock_producer.send.call_args
            assert call_args[0][0] == "test.topic"
            assert call_args[1]["key"] == "sess_123"


def test_kafka_error_does_not_raise():
    with patch.dict(os.environ, {"KAFKA_BOOTSTRAP_SERVERS": "localhost:9092"}):
        mock_producer = MagicMock()
        mock_producer.send.side_effect = RuntimeError("Broker down")
        with patch("services.kafka_emitter.get_kafka_producer", return_value=mock_producer):
            res = emit_event_to_kafka({"stage": STAGES.CONTENT_FLAGGED, "node_id": "n1"})
            assert res["status"] == "error"
            assert "Broker down" in res["error"]


def test_record_event_streams_to_telemetry():
    ls = {}
    with patch("services.events.emit_telemetry") as mock_telemetry:
        record_event(ls, STAGES.NODE_MASTERED, node_id="n1", score=100.0)
        assert len(ls["events"]) == 1
        mock_telemetry.assert_called_once()
        event_arg = mock_telemetry.call_args[0][0]
        assert event_arg["stage"] == STAGES.NODE_MASTERED
        assert event_arg["node_id"] == "n1"
        assert event_arg["score"] == 100.0


def test_emit_telemetry_coordinates_kafka():
    from services.telemetry import emit_telemetry
    with patch("services.telemetry.emit_event_to_kafka", return_value={"status": "skipped"}) as mock_kafka:
        res = emit_telemetry({"stage": STAGES.LESSON_OPENED, "node_id": "n2"})
        mock_kafka.assert_called_once()
        assert res["event_stage"] == STAGES.LESSON_OPENED
        assert res["kafka_status"] == "skipped"
