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
                "MATCH (a:Skill {id: $src}) "
                "MATCH (b:Skill {id: $tgt}) "
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


def write_session_progress(session_id: str, node_state: dict):
    """Write the full node_state dict for a session into Neo4j."""
    from persistence.neo4j_db import get_driver
    try:
        with get_driver().session() as session:
            session.run(
                "MERGE (s:Session {id: $session_id}) "
                "WITH s "
                "UNWIND $states AS st "
                "MERGE (p:Progress {session_id: $session_id, node_id: st.id}) "
                "MERGE (s)-[:HAS_PROGRESS]->(p) "
                "WITH p, st "
                "MATCH (skill:Skill {id: st.id}) "
                "MERGE (p)-[:FOR_SKILL]->(skill) "
                "SET p.status = st.status, "
                "    p.remediation_pending = coalesce(st.remediation_pending, false)",
                session_id=session_id,
                states=[{"id": nid, **state} for nid, state in node_state.items()]
            )
    except Exception as e:
        logger.error(f"Failed to write session progress to Neo4j: {e}")


def get_completed_and_locked_nodes(session_id: str) -> tuple[set, set]:
    """Execute Cypher to determine completed and locked nodes for a session.
    A node is completed if it is mastered AND all its prerequisites are mastered.
    A node is locked if any prerequisite is incomplete, OR if it has a pending remediation
    and its remedial prerequisites are not yet completed."""
    from persistence.neo4j_db import get_driver
    try:
        with get_driver().session() as session:
            # Cypher for completed nodes
            completed = session.run(
                """
                MATCH (s:Session {id: $session_id})-[:HAS_PROGRESS]->(p:Progress)-[:FOR_SKILL]->(n:Skill)
                WHERE p.status = 'mastered'
                OPTIONAL MATCH (unmastered_p:Progress)-[:FOR_SKILL]->(unmastered:Skill)-[:PREREQUISITE*1..]->(n)
                WHERE unmastered_p.session_id = $session_id AND unmastered_p.status <> 'mastered'
                WITH n, count(unmastered) AS incomplete_prereqs
                WHERE incomplete_prereqs = 0
                RETURN n.id AS id
                """,
                session_id=session_id
            )
            completed_ids = {record["id"] for record in completed}
            
            # Cypher for locked nodes
            locked = session.run(
                """
                MATCH (s:Session {id: $session_id})-[:HAS_PROGRESS]->(p:Progress)-[:FOR_SKILL]->(n:Skill)
                OPTIONAL MATCH (unmastered_p:Progress)-[:FOR_SKILL]->(unmastered:Skill)-[:PREREQUISITE*1..]->(n)
                WHERE unmastered_p.session_id = $session_id AND unmastered_p.status <> 'mastered'
                
                OPTIONAL MATCH (p)-[:FOR_SKILL]->(n)<-[:PREREQUISITE]-(direct_prereq:Skill)<-[:FOR_SKILL]-(dp:Progress {session_id: $session_id})
                
                WITH n, p, count(unmastered) AS incomplete_prereqs, collect(dp.status) AS direct_prereq_statuses
                
                WHERE incomplete_prereqs > 0 
                   OR (p.remediation_pending = true AND (size(direct_prereq_statuses) = 0 OR any(status IN direct_prereq_statuses WHERE status <> 'mastered')))
                
                RETURN n.id AS id
                """,
                session_id=session_id
            )
            locked_ids = {record["id"] for record in locked}
            
            return completed_ids, locked_ids
    except Exception as e:
        logger.error(f"Failed to query completion status from Neo4j: {e}")
        return set(), set()
