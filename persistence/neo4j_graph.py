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
