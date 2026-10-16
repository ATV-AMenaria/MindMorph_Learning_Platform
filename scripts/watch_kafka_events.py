"""Live Kafka Event Consumer for MindMorph.

Run this script in a terminal to watch learning journey events stream in real time:
    conda activate mm
    python scripts/watch_kafka_events.py
"""
import json
import os
import sys

# Ensure UTF-8 stdout on Windows PowerShell / cmd
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import dotenv
dotenv.load_dotenv(os.path.join(ROOT, ".env"))

try:
    from kafka import KafkaConsumer
except ImportError:
    print("Error: kafka-python-ng is not installed. Run: pip install kafka-python-ng")
    sys.exit(1)

servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
topic = os.getenv("MINDMORPH_KAFKA_TOPIC", "mindmorph.learning_events")

print("=" * 60)
print("[LIVE KAFKA CONSUMER] MindMorph Learning Events")
print(f"Connecting to broker: {servers}")
print(f"Listening on topic : {topic}")
print("Waiting for real-time student events... (Press Ctrl+C to stop)")
print("=" * 60)

try:
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=servers.split(","),
        auto_offset_reset="latest",
        enable_auto_commit=True,
        group_id="mindmorph_live_watcher",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else "None",
    )

    for msg in consumer:
        event = msg.value
        stage = event.get("stage", "unknown")
        ts = event.get("ts", "")
        session = event.get("session_id", "N/A")
        node = event.get("node_id", "N/A")
        score = event.get("score")

        print(f"\n>>> [EVENT RECEIVED] Stage: {stage}")
        print(f"    Timestamp  : {ts}")
        print(f"    Key/Session: {msg.key}")
        if node != "N/A":
            print(f"    Node ID    : {node}")
        if score is not None:
            print(f"    Score      : {score}")
        print(f"    Payload    : {json.dumps(event, indent=2)}")
except KeyboardInterrupt:
    print("\nWatcher stopped by user.")
except Exception as e:
    print(f"\nError connecting to Kafka: {e}")
    sys.exit(1)
