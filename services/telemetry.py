"""Central Telemetry and Observability Module for MindMorph.

Coordinates real-time journey instrumentation across:
1. LangSmith Tracing (@traceable spans for learning events, LLM executions, and routing decisions)
2. Apache Kafka Event Streaming (real-time student milestone pub/sub via services.kafka_emitter)
3. Zero-infra graceful fallback for local development
"""
import logging
from typing import Any

from services.kafka_emitter import emit_event_to_kafka, is_kafka_configured

logger = logging.getLogger(__name__)

try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


@traceable(name="learning_event_telemetry", run_type="tool")
def emit_telemetry(event: dict[str, Any]) -> dict[str, Any]:
    """Emit a structured learning journey event to telemetry sinks.

    Captures the event span in LangSmith and pipes the payload to the Kafka
    streaming broker (if configured).

    Args:
        event: Dictionary containing event metadata ('stage', 'ts', 'session_id', etc.)

    Returns:
        Telemetry delivery summary dict.
    """
    stage = event.get("stage", "unknown")
    kafka_result = emit_event_to_kafka(event)
    
    return {
        "event_stage": stage,
        "kafka_status": kafka_result.get("status"),
        "kafka_detail": kafka_result,
        "is_kafka_configured": is_kafka_configured(),
    }
