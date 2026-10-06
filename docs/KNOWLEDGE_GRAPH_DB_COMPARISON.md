# Knowledge Graph DB Comparison & Neo4j Implementation — MindMorph

---

## 1. Cloud-Agnostic, Free Knowledge Graph Databases

All databases below are self-hostable via Docker (no forced vendor), free forever on
self-hosted Docker, and accessible from Python via a pip-installable driver.

| # | Database | Docker Image | Free Cloud Tier | License |
|---|---|---|---|---|
| 1 | **Neo4j Community** | `neo4j:5-community` | AuraDB Free (200k nodes) | GPLv3 |
| 2 | **Memgraph Community** | `memgraph/memgraph` | None | BSL 1.1 |
| 3 | **FalkorDB** | `falkordb/falkordb` | FalkorDB Cloud (RAM-limited) | Redis SSPL / MIT |
| 4 | **Apache AGE** | runs inside Postgres | None | Apache 2.0 |
| 5 | **ArangoDB Community** | `arangodb:latest` | 14-day trial only | BSL 1.1 |
| 6 | **Dgraph** | `dgraph/standalone` | Dgraph Cloud (free tier) | Apache 2.0 |
| 7 | **JanusGraph** | `janusgraph/janusgraph` | None | Apache 2.0 |

---

## 2. Full Comparison

### 2.1 Query Language

| Database | Language | Standard? |
|---|---|---|
| Neo4j | Cypher | De facto industry standard |
| Memgraph | OpenCypher | Open spec, ~95% Cypher-compatible |
| FalkorDB | OpenCypher | Same as Memgraph |
| Apache AGE | openCypher inside SQL | Awkward hybrid |
| ArangoDB | AQL (proprietary) | Not Cypher-compatible |
| Dgraph | DQL / GraphQL+- | Proprietary |
| JanusGraph | Gremlin (TinkerPop) | Open but verbose |

### 2.2 Feature Matrix

| Feature | Neo4j | Memgraph | FalkorDB | Apache AGE | ArangoDB | Dgraph | JanusGraph |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Graph algorithms library** | ✅ GDS | ✅ MAGE | ⚠️ Basic | ❌ | ✅ | ⚠️ | ❌ |
| **LangGraph checkpointer** | ✅ Official | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **LangChain integration** | ✅ `langchain-neo4j` | ⚠️ Via Bolt | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Python driver maturity** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| **Rich node properties** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **ACID transactions** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Uses existing Postgres** | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Query speed (small graphs)** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| **Free node/edge limit** | None (Docker) | None | RAM only | None | None | None | None |
| **Documentation quality** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| **Community size** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |

### 2.3 Pros and Cons Per Database

#### Neo4j Community Edition
| ✅ Pros | ❌ Cons |
|---|---|
| Only DB with an official LangGraph checkpointer | Single-instance; no HA in Community |
| `langchain-neo4j` — plug into LangGraph in 2 lines | GDS not on AuraDB Free (need self-hosted Docker) |
| 15+ years in production, largest graph DB community | Cypher is Neo4j-specific (not fully portable) |
| GDS library: cosine similarity, PageRank, path finding | New Docker service required |
| AuraDB Free for zero-infra cloud pilot | GPLv3 license (fine for this use case) |
| 1:1 fit with MindMorph's `{nodes, edges}` data model | — |

#### Memgraph Community Edition
| ✅ Pros | ❌ Cons |
|---|---|
| No node/edge limits on Community Edition | No official LangGraph checkpointer |
| MAGE: 50+ algorithms (jaccard, cosine, PageRank) | No managed free cloud tier |
| Same Bolt protocol as Neo4j — `neo4j` Python driver works unchanged | Smaller community |
| In-memory: sub-millisecond queries | Enterprise HA/RBAC is paid |

#### FalkorDB
| ✅ Pros | ❌ Cons |
|---|---|
| Fastest query speed (in-memory over Redis) | No LangGraph checkpointer |
| Redis-compatible | No GDS / similarity algorithms |
| Free cloud tier | Smallest community of the top three |

#### Apache AGE
| ✅ Pros | ❌ Cons |
|---|---|
| Zero new infra — runs inside existing Postgres | Cypher-in-SQL is very verbose |
| Shares existing SQLAlchemy connection pool | No graph algorithm library |
| | Slower than purpose-built graph engines |

#### ArangoDB, Dgraph, JanusGraph
All three lack any LangChain/LangGraph integration, use proprietary or verbose query
languages (AQL, DQL, Gremlin), and are significantly more complex to operate than Neo4j
or Memgraph for the scale MindMorph requires. Not recommended.

---

## 3. Winner: Neo4j Community Edition

**Neo4j is the best choice for MindMorph.** Not because of brand recognition — because of
three facts specific to this codebase.

### Reason 1 — The only DB with an official LangGraph integration

Your pipeline is built on LangGraph (`langgraph.graph.StateGraph`). Neo4j ships
`langchain-neo4j`, which includes a **LangGraph persistence checkpointer** — it stores
every graph execution state in Neo4j automatically. No other database on this list has this.
Every alternative requires writing a custom adapter from scratch.

### Reason 2 — Your data model is already a property graph

Your Consensus agent already produces structured output via `skill_graph_schema.py`:

```python
SkillGraph(
    nodes=[SkillNode(id="python_basics", label="Python Basics", level="foundational"), ...],
    edges=[SkillEdge(source="python_basics", target="oop", relation="prerequisite"), ...]
)
```

This is literally a property graph. Each `SkillNode` becomes a Neo4j node. Each
`SkillEdge` becomes a `[:PREREQUISITE]` relationship. **Zero transformation needed.**
The Pydantic model maps directly to Cypher `MERGE` / `CREATE` operations.

### Reason 3 — It replaces existing Python logic with database-native queries

Current `skill_graph_adapt.py` implements DFS reachability in Python:

```python
def _reaches(edges_by_source, start, target):   # Python DFS
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur == target: return True
        ...
```

In Neo4j this is one line:

```cypher
MATCH path = (a:Skill {id: $start})-[:PREREQUISITE*]->(b:Skill {id: $target})
RETURN count(path) > 0 AS reachable
```

The `prune_dangling_edges()` function also becomes a native Cypher `DELETE` —
the database enforces referential integrity automatically.

---

## 4. What Is Already in the Project vs What Needs to Be Built

### 4.1 Already Exists — Do Not Touch

| Component | File | Notes |
|---|---|---|
| Skill graph data model | `agents/consensus/skill_graph_schema.py` | Maps directly to Neo4j — no changes |
| Graph builder | `agents/consensus/consensus_agent.py` | Output becomes the write source |
| Graph adaptation | `graph/skill_graph_adapt.py` | Logic can migrate to Cypher queries |
| Mermaid renderer | `graph/skill_graph_render.py` | Unchanged — still receives `{nodes, edges}` |
| Full LangGraph pipeline | `graph/learning_plan_graph.py` | Unchanged — runs on cache miss only |
| Session persistence abstraction | `persistence/repository.py` | Abstract interface — already extensible |
| Alembic migration setup | `alembic/` | Exists — add new migration |
| Docker Compose | `docker-compose.yml` | Exists — add Neo4j service |

### 4.2 Needs to Be Built

| Component | File | Effort |
|---|---|---|
| Neo4j service in Docker | `docker-compose.yml` | +10 lines |
| Neo4j driver singleton | `persistence/neo4j_db.py` | New file, ~25 lines |
| Graph write/read functions | `persistence/neo4j_graph.py` | New file, ~80 lines |
| `graph_cache` Alembic migration | `alembic/versions/xxx_graph_cache.py` | ~20 lines |
| Cache lookup in service layer | `services/learning_service.py` | +~50 lines (wrapper functions) |
| Environment config | `.env` | +3 lines |
| New pip dependencies | `requirements.txt` | +2 lines |

---

## 5. Technical Architecture — Full Neo4j Implementation

### 5.1 `docker-compose.yml` — Add Neo4j Service

```yaml
services:
  db:                          # existing Postgres — unchanged
    image: pgvector/pgvector:pg16
    ...

  neo4j:                       # ADD THIS
    image: neo4j:5-community
    container_name: mindmorph-neo4j
    environment:
      NEO4J_AUTH: neo4j/mindmorph_neo4j
      NEO4J_PLUGINS: '["graph-data-science"]'   # enables GDS algorithms
    ports:
      - "7474:7474"            # Neo4j Browser (web UI at http://localhost:7474)
      - "7687:7687"            # Bolt protocol (Python driver)
    volumes:
      - mindmorph_neo4j_data:/data
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p mindmorph_neo4j 'RETURN 1'"]
      interval: 10s
      timeout: 10s
      retries: 10

volumes:
  mindmorph_pgdata:
  mindmorph_neo4j_data:        # ADD THIS
```

### 5.2 `.env` — Three New Lines

```dotenv
# Neo4j (alongside existing DATABASE_URL)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mindmorph_neo4j
```

### 5.3 New File: `persistence/neo4j_db.py`

```python
"""Neo4j driver — lazy singleton, mirrors the pattern in persistence/db.py."""
import os
from typing import Optional
from neo4j import GraphDatabase, Driver

_driver: Optional[Driver] = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        uri      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        user     = os.getenv("NEO4J_USER",     "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "mindmorph_neo4j")
        _driver  = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def close_driver():
    global _driver
    if _driver:
        _driver.close()
        _driver = None
```

### 5.4 New File: `persistence/neo4j_graph.py`

```python
"""Read/write SkillGraph to/from Neo4j. Each topic gets ONE shared graph."""
import logging
from typing import Optional
from persistence.neo4j_db import get_driver

logger = logging.getLogger(__name__)


def topic_graph_exists(canonical: str) -> bool:
    """Return True if a reviewed SkillGraph already exists for this canonical topic."""
    with get_driver().session() as session:
        result = session.run(
            "MATCH (t:Topic {canonical: $topic}) "
            "WHERE t.graph_built = true RETURN count(t) AS n",
            topic=canonical,
        )
        return result.single()["n"] > 0


def write_skill_graph(canonical: str, skill_graph: dict) -> None:
    """Write a SkillGraph {nodes, edges} dict into Neo4j as real nodes and relationships."""
    with get_driver().session() as session:
        # 1. Create/update the Topic anchor node
        session.run(
            "MERGE (t:Topic {canonical: $topic}) SET t.graph_built = true",
            topic=canonical,
        )
        # 2. Create Skill nodes with full properties
        for node in skill_graph.get("nodes", []):
            session.run(
                "MERGE (s:Skill {id: $id}) "
                "SET s.label = $label, s.level = $level, s.description = $desc",
                id=node["id"], label=node.get("label", ""),
                level=node.get("level", ""), desc=node.get("description", ""),
            )
            # Link each Skill to its Topic
            session.run(
                "MATCH (t:Topic {canonical: $topic}), (s:Skill {id: $id}) "
                "MERGE (t)-[:HAS_SKILL]->(s)",
                topic=canonical, id=node["id"],
            )
        # 3. Create PREREQUISITE relationships
        for edge in skill_graph.get("edges", []):
            session.run(
                "MATCH (a:Skill {id: $src}), (b:Skill {id: $tgt}) "
                "MERGE (a)-[:PREREQUISITE]->(b)",
                src=edge["source"], tgt=edge["target"],
            )
    logger.info("neo4j: wrote skill graph for %r (%d nodes)", canonical,
                len(skill_graph.get("nodes", [])))


def read_skill_graph(canonical: str) -> Optional[dict]:
    """Read a SkillGraph back from Neo4j as a {nodes, edges} dict."""
    with get_driver().session() as session:
        nodes_res = session.run(
            "MATCH (:Topic {canonical: $topic})-[:HAS_SKILL]->(s:Skill) "
            "RETURN s.id AS id, s.label AS label, s.level AS level, "
            "s.description AS description",
            topic=canonical,
        )
        nodes = [dict(r) for r in nodes_res]
        if not nodes:
            return None

        edges_res = session.run(
            "MATCH (:Topic {canonical: $topic})-[:HAS_SKILL]->(a:Skill)"
            "-[:PREREQUISITE]->(b:Skill) "
            "RETURN a.id AS source, b.id AS target",
            topic=canonical,
        )
        edges = [
            {"source": r["source"], "target": r["target"], "relation": "prerequisite"}
            for r in edges_res
        ]
    return {"nodes": nodes, "edges": edges}
```

### 5.5 Alembic Migration — `graph_cache` Table

```bash
alembic revision --autogenerate -m "add graph_cache"
```

Edit the generated file:

```python
from sqlalchemy.dialects import postgresql

def upgrade():
    op.create_table(
        "graph_cache",
        sa.Column("cache_key",      sa.Text, primary_key=True),
        sa.Column("canonical_topic",sa.Text, nullable=False),
        sa.Column("query_text",     sa.Text, nullable=False),
        sa.Column("skill_graph",    postgresql.JSONB, nullable=False),
        sa.Column("hit_count",      sa.Integer, server_default="0"),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_hit_at",    sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

def downgrade():
    op.drop_table("graph_cache")
```

### 5.6 Modify `services/learning_service.py` — Cache Wrapper

Add these functions near the top of the file, after the existing imports:

```python
import hashlib
import re

def _normalize_query(q: str) -> str:
    """Produce a canonical topic key from a user query for cache matching."""
    return re.sub(r"\s+", " ", q.lower().strip())


def _get_graph_from_cache(canonical: str) -> Optional[dict]:
    """Look up an existing skill graph from Postgres graph_cache (Phase 1).
    Falls through to None on any failure — pipeline runs as normal fallback."""
    try:
        from sqlalchemy import text
        from persistence.db import get_sessionmaker
        cache_key = hashlib.sha256(canonical.encode()).hexdigest()
        with get_sessionmaker()() as session:
            row = session.execute(
                text("SELECT skill_graph FROM graph_cache WHERE cache_key = :key"),
                {"key": cache_key},
            ).fetchone()
            if row:
                session.execute(
                    text("UPDATE graph_cache SET hit_count = hit_count + 1, "
                         "last_hit_at = now() WHERE cache_key = :key"),
                    {"key": cache_key},
                )
                session.commit()
                logger.info("graph_cache: HIT for %r", canonical)
                return dict(row[0])
    except Exception:
        logger.warning("graph_cache lookup failed (non-fatal)", exc_info=True)
    return None


def _save_graph_to_cache(canonical: str, query_text: str, skill_graph: dict) -> None:
    """Save a new skill graph to Postgres graph_cache and, if Neo4j is running, to Neo4j."""
    try:
        from sqlalchemy import text
        from persistence.db import get_sessionmaker
        cache_key = hashlib.sha256(canonical.encode()).hexdigest()
        with get_sessionmaker()() as session:
            session.execute(
                text("INSERT INTO graph_cache "
                     "(cache_key, canonical_topic, query_text, skill_graph) "
                     "VALUES (:key, :topic, :query, :graph::jsonb) "
                     "ON CONFLICT (cache_key) DO NOTHING"),
                {"key": cache_key, "topic": canonical,
                 "query": query_text, "graph": __import__("json").dumps(skill_graph)},
            )
            session.commit()
    except Exception:
        logger.warning("graph_cache write failed (non-fatal)", exc_info=True)

    # Phase 2: also write to Neo4j (non-fatal if not configured)
    try:
        from persistence.neo4j_graph import write_skill_graph
        write_skill_graph(canonical, skill_graph)
    except Exception:
        logger.warning("neo4j write failed (non-fatal — Postgres cache still saved)")
```

Then in the existing `start_session()` function, add the cache check at the top:

```python
def start_session(user_query: str, format_type: str, ...) -> dict:
    canonical = _normalize_query(user_query)

    # ── Cache check: return immediately if graph already built ────────────────
    cached_graph = _get_graph_from_cache(canonical)
    if cached_graph:
        ls = new_learning_session({"skill_graph": cached_graph}, format_type)
        if ls:
            return {"route": "SCOUT", "learning_session": ls,
                    "cache_hit": True, "final_content": None, "exercise": None}
    # ─────────────────────────────────────────────────────────────────────────

    # ... existing pipeline code runs here (unchanged) ...
    final_state = _run_async(_get_orchestration_graph().ainvoke(...))
    result = _route_final_state(final_state, format_type)

    # ── Save the new graph to cache after pipeline completes ──────────────────
    if result.get("route") == "SCOUT":
        ls = result.get("learning_session")
        if ls:
            sg = ls.get("skill_graph")
            if sg:
                _save_graph_to_cache(canonical, user_query, sg)
    # ─────────────────────────────────────────────────────────────────────────

    return result
```

### 5.7 `requirements.txt` — Two New Lines

```
neo4j>=5.0          # Official Neo4j Python driver (Bolt protocol)
langchain-neo4j     # LangChain/LangGraph Neo4j integration
```

---

## 6. Data Model in Neo4j — What the Graph Looks Like

```
(:Topic {canonical:"python", graph_built:true})
    │
    ├──[:HAS_SKILL]──▶ (:Skill {id:"python_basics",   label:"Python Basics",   level:"foundational"})
    ├──[:HAS_SKILL]──▶ (:Skill {id:"oop",              label:"OOP",             level:"intermediate"})
    ├──[:HAS_SKILL]──▶ (:Skill {id:"file_io",          label:"File I/O",        level:"intermediate"})
    └──[:HAS_SKILL]──▶ (:Skill {id:"advanced_python",  label:"Advanced Python", level:"advanced"})

Prerequisite edges (the dependency graph):
  (:Skill {id:"python_basics"}) ──[:PREREQUISITE]──▶ (:Skill {id:"oop"})
  (:Skill {id:"python_basics"}) ──[:PREREQUISITE]──▶ (:Skill {id:"file_io"})
  (:Skill {id:"oop"})           ──[:PREREQUISITE]──▶ (:Skill {id:"advanced_python"})
  (:Skill {id:"file_io"})       ──[:PREREQUISITE]──▶ (:Skill {id:"advanced_python"})

Per-user progress (separate — never on the shared graph):
  (:Session {id:"sess-user-A"}) ──[:USES_TOPIC]──▶ (:Topic {canonical:"python"})
  (:Progress {session:"sess-user-A", skill:"python_basics", score:0.92, status:"mastered"})
  (:Progress {session:"sess-user-A", skill:"oop",            score:0.40, status:"in_progress"})
```

**Critical rule:** `(:Skill)` nodes are **global shared knowledge**. `(:Progress)` nodes
are **per-session**. No user ever touches another user's mastery data.

---

## 7. Cypher Queries That Replace Existing Python Logic

| Existing Python code | Neo4j Cypher replacement |
|---|---|
| `_reaches(edges, "python_basics", "advanced_python")` in `skill_graph_adapt.py` | `MATCH p=(:Skill {id:"python_basics"})-[:PREREQUISITE*]->(:Skill {id:"advanced_python"}) RETURN count(p)>0` |
| `prereqs_by_node(skill_graph)` in `completion.py` | `MATCH (s:Skill)<-[:PREREQUISITE]-(p:Skill) RETURN s.id, collect(p.id)` |
| `prune_dangling_edges(skill_graph)` in `skill_graph_adapt.py` | `MATCH (a)-[r:PREREQUISITE]->(b) WHERE NOT exists(b.id) DELETE r` |
| Full blob load → render Mermaid | `MATCH (t:Topic {canonical:$t})-[:HAS_SKILL]->(s) RETURN s` + `MATCH (a)-[:PREREQUISITE]->(b) RETURN a.id, b.id` |
| Full blob rewrite on mastery update | `MATCH (p:Progress {session:$sid, skill:$nid}) SET p.score=$score, p.status=$status` |

---

## 8. Implementation Steps — Ordered by Priority

```
STEP 1 — TODAY (~1 day, no new infra)
──────────────────────────────────────
  [ ] Run: alembic revision --autogenerate -m "add graph_cache"
  [ ] Edit the migration to create the graph_cache table (§5.5)
  [ ] Run: alembic upgrade head
  [ ] Add _normalize_query / _get_graph_from_cache / _save_graph_to_cache
      to services/learning_service.py (§5.6)
  [ ] Wrap start_session() with the cache check
  [ ] Test: same query twice → second call logs "graph_cache: HIT"
  ✅ Result: any repeated query from any account skips the pipeline

STEP 2 — NEXT SPRINT (~2-3 days, adds Neo4j)
──────────────────────────────────────────────
  [ ] Add neo4j service to docker-compose.yml (§5.1)
  [ ] Add NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD to .env (§5.2)
  [ ] Create persistence/neo4j_db.py (§5.3)
  [ ] Create persistence/neo4j_graph.py (§5.4)
  [ ] Add neo4j + langchain-neo4j to requirements.txt (§5.7)
  [ ] Run: docker compose up -d neo4j
  [ ] Run: pip install neo4j langchain-neo4j
  [ ] Verify: visit http://localhost:7474, login neo4j/mindmorph_neo4j
  ✅ Result: skill graphs stored as real graph nodes in Neo4j

STEP 3 — SCALE (~1 week, full graph-native architecture)
──────────────────────────────────────────────────────────
  [ ] Modify start_session() to call topic_graph_exists() (from neo4j_graph.py)
      as the primary cache check BEFORE Postgres hash lookup
  [ ] Modify read path to call read_skill_graph() instead of deserialising JSONB blob
  [ ] Wire the official LangGraph Neo4j checkpointer (from langchain-neo4j)
  [ ] Migrate mastery/progress writes from JSONB blob to (:Progress) Neo4j nodes
  [ ] Replace Python DFS in skill_graph_adapt.py with Cypher queries
  ✅ Result: full property graph — one (:Skill) graph shared globally per topic
```

---

*File: `docs/KNOWLEDGE_GRAPH_DB_COMPARISON.md` | MindMorph Learning Platform*

Referenced files:
- [`agents/consensus/skill_graph_schema.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/agents/consensus/skill_graph_schema.py)
- [`graph/skill_graph_adapt.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/graph/skill_graph_adapt.py)
- [`graph/learning_plan_graph.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/graph/learning_plan_graph.py)
- [`services/learning_service.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/services/learning_service.py)
- [`persistence/repository.py`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/persistence/repository.py)
- [`docker-compose.yml`](file:///c:/Users/praku/OneDrive/Desktop/MindMorph_Learning_Platform/docker-compose.yml)

