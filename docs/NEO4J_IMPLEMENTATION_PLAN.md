# Neo4j Implementation Plan — MindMorph

This document outlines the step-by-step plan to integrate Neo4j into MindMorph as the canonical graph store, moving away from purely relying on a JSONB blob in Postgres. The implementation is split into three phases to deliver immediate value (cost savings via caching) before migrating the full architecture.

---

## Phase 1: Immediate Caching via Postgres (The "Quick Win")

**Goal:** Stop calling the 6-agent LLM pipeline for duplicate queries.
**Why first?** It requires zero new infrastructure, can be implemented today, and immediately saves tokens/costs.

### Step 1.1: Database Migration
- [x] Run Alembic to generate a new migration: `alembic revision --autogenerate -m "add graph_cache"`
- [x] Define the `graph_cache` table in the migration with columns:
  - `cache_key` (Text, Primary Key, stores SHA-256 hash)
  - `canonical_topic` (Text, stores normalized query)
  - `query_text` (Text, the original user query)
  - `skill_graph` (JSONB, the actual output graph)
  - `hit_count` (Integer)
- [x] Run `alembic upgrade head` to apply the table to the local Postgres db.

### Step 1.2: Cache Logic in Service Layer
- [x] In `services/learning_service.py`, implement `_normalize_query(q: str)` to strip punctuation and lowercase the query.
- [x] Implement `_get_graph_from_cache(canonical: str)` to do a DB lookup in Postgres.
- [x] Implement `_save_graph_to_cache(canonical: str, query_text: str, skill_graph: dict)` to write new graphs to Postgres on cache miss.

### Step 1.3: Wire the Cache to the Pipeline
- [x] Update `start_session()` in `learning_service.py` to check the cache **before** invoking the orchestration graph.
- [x] If there's a cache hit, skip the LangGraph pipeline entirely and construct the session using the cached `skill_graph`.
- [x] If there's a cache miss, run the pipeline and call `_save_graph_to_cache()` on the result.

---

## Phase 2: Introducing Neo4j Infrastructure (The "Shadow Write")

**Goal:** Start writing data to a real graph database alongside Postgres.
**Why next?** Bootstraps the Neo4j environment without disrupting the existing read path.

### Step 2.1: Infrastructure Setup
- [x] Update `docker-compose.yml` to include the `neo4j:5-community` service.
- [x] Expose ports `7474` (HTTP Browser) and `7687` (Bolt Protocol).
- [x] Map a Docker volume for Neo4j data persistence (`mindmorph_neo4j_data`).
- [x] Update `.env` to include `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`.
- [x] Start the new service via `docker compose up -d neo4j`.

### Step 2.2: Python Driver Integration
- [x] Add `neo4j>=5.0` and `langchain-neo4j` to `requirements.txt`.
- [x] Create `persistence/neo4j_db.py` to act as a lazy connection singleton (matching the pattern in `persistence/db.py`).

### Step 2.3: Graph Write Operations
- [x] Create `persistence/neo4j_graph.py`.
- [x] Implement `write_skill_graph(canonical: str, skill_graph: dict)` to translate the dict into Cypher `MERGE` commands:
  - Create the `(:Topic)` anchor node.
  - Create the `(:Skill)` nodes and link them to the Topic.
  - Create the `[:PREREQUISITE]` edges between skills.
- [x] Update `_save_graph_to_cache()` in `learning_service.py` to call `write_skill_graph()` right after writing to Postgres.

---

## Phase 3: Full Graph-Native Architecture (The "Cutover")

**Goal:** Make Neo4j the primary source of truth for all graph data and operations.
**Why last?** Replaces existing Python logic and requires thorough testing.

### Step 3.1: Read from Neo4j
- [x] Implement `read_skill_graph(canonical: str)` in `neo4j_graph.py` to query nodes and edges back from the graph and construct the `{nodes, edges}` dict.
- [x] Implement `topic_graph_exists(canonical: str)` in `neo4j_graph.py`.
- [x] Modify `start_session()` to use `topic_graph_exists()` and `read_skill_graph()` from Neo4j as the primary cache check, falling back to Postgres if needed.

### Step 3.2: LangGraph Checkpointer Integration
- [x] Import and wire up the official LangGraph Neo4j checkpointer (from `langchain-neo4j`) into the `StateGraph` compilation step in `graph/learning_plan_graph.py`.
- [x] This replaces the default in-memory or Postgres checkpointer, making pipeline execution fully traceable in Neo4j.

### Step 3.3: Replace Python DFS with Cypher
- [x] In `graph/skill_graph_adapt.py`, replace the Python-based `_reaches()` depth-first search function with a native Cypher path query:
  `MATCH path = (a:Skill {id: $start})-[:PREREQUISITE*]->(b:Skill {id: $target})`
- [x] Replace `prune_dangling_edges()` with a Cypher query that automatically deletes orphaned relationships.
- [x] Update `services/completion.py` to use Cypher to compute available, locked, and in-progress nodes directly.

### Step 3.4: Migrate User Progress
- [x] Transition mastery state out of the JSONB blob.
- [x] Model user progress in Neo4j using `(:Session)` nodes and `(:Progress)` nodes linked to the shared `(:Skill)` nodes.
- [x] Refactor `services/mastery.py` to update mastery scores via Cypher `SET` commands on the `(:Progress)` nodes instead of rewriting the entire JSONB session.

---

## Phase 4: Testing & Verification

**Goal:** Ensure the system behaves as expected during integrations.

### Step 4.1: Integration Tests ("Dragon Test")
- [x] Write integration tests to verify the cache hit correctly short-circuits the pipeline.
- [x] Write tests verifying that querying the same topic twice does not invoke the LLM agents.
- [x] Verify that invalid or noisy queries (e.g., "Python! " vs "python") map to the same canonical cache key.

### Step 4.2: Graphics / Visual Verification
- [x] Ensure that the Mermaid graph rendered in the frontend (`SkillGraph` display) continues to function perfectly when reading from the Neo4j backend.
- [x] Compare the frontend visualization from a cache-hit (Neo4j read) vs a fresh generation to ensure no edge or node properties are lost in translation.

---

*File: `docs/NEO4J_IMPLEMENTATION_PLAN.md` | MindMorph Learning Platform*
