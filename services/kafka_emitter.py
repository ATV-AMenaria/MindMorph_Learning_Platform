"""Kafka Telemetry Event Emitter for MindMorph.

Connects to Apache Kafka / Confluent / AWS MSK / GCP PubSub (when configured via
KAFKA_BOOTSTRAP_SERVERS) and publishes learning journey events in real time.
Traces every event directly into LangSmith via @traceable.

Safe by construction:
  - If KAFKA_BOOTSTRAP_SERVERS is unset, it behaves as a silent no-op.
  - If the broker is unreachable or kafka-python is not installed, it logs a warning
    and NEVER interrupts the student's learning session.
"""
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

_producer = None
_producer_checked = False


def is_kafka_configured() -> bool:
    """Return True if a Kafka broker is configured in the environment."""
    return bool(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip())


def get_kafka_producer():
    """Lazy-load the Kafka producer client if KAFKA_BOOTSTRAP_SERVERS is set."""
    global _producer, _producer_checked
    if _producer_checked:
        return _producer

    _producer_checked = True
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not servers:
        return None

    try:
        from kafka import KafkaProducer
        logger.info("Initializing Kafka producer for servers: %s", servers)
        _producer = KafkaProducer(
            bootstrap_servers=servers.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: str(k).encode("utf-8") if k else None,
            retries=2,
            request_timeout_ms=3000,
        )
        logger.info("Kafka producer connected successfully.")
    except ImportError:
        logger.info("kafka-python package not installed. Run 'pip install kafka-python' to stream to Kafka.")
        _producer = None
    except Exception as e:
        logger.warning("Could not connect to Kafka broker (%s): %s", servers, e)
        _producer = None

    return _producer


@traceable(name="kafka_event_emitter", run_type="tool")
def emit_event_to_kafka(event: dict[str, Any]) -> dict[str, Any]:
    """Emit a structured learning event to Kafka and trace it in LangSmith.

    Args:
        event: Dictionary containing 'stage', 'ts', and arbitrary event fields.

    Returns:
        Status summary dict visible in LangSmith traces.
    """
    topic = os.getenv("MINDMORPH_KAFKA_TOPIC", "mindmorph.learning_events")
    stage = event.get("stage", "unknown")

    if not is_kafka_configured():
        return {
            "status": "skipped",
            "reason": "KAFKA_BOOTSTRAP_SERVERS is not configured (local/dev mode)",
            "event_stage": stage,
        }

    producer = get_kafka_producer()
    if producer is None:
        return {
            "status": "degraded",
            "reason": "Kafka broker unreachable or kafka-python library missing",
            "event_stage": stage,
        }

    try:
        # Use node_id or session_id as partition key if available
        key = event.get("session_id") or event.get("node_id") or stage
        producer.send(topic, key=key, value=event)
        logger.debug("Emitted event %r to Kafka topic %r", stage, topic)
        return {
            "status": "emitted",
            "topic": topic,
            "event_stage": stage,
            "partition_key": key,
        }
    except Exception as e:
        logger.warning("Failed to emit event %r to Kafka: %s", stage, e)
        return {
            "status": "error",
            "error": str(e),
            "event_stage": stage,
        }
