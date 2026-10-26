"""Test-wide setup. Set before any test imports `config`."""
import os

# Default the §6.3 content tail OFF for the suite so building a content graph never fires the real
# example/visual LLM agents. test_content_tail.py opts back in (monkeypatch) to exercise the tail.
os.environ.setdefault("MINDMORPH_RICH_CONTENT", "0")
