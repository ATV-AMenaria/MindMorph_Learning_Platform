import { expect, test } from "@playwright/test";
import {
  completeNodeIds,
  displayStatus,
  incompletePrereqLabels,
  lockedNodeIds,
} from "../lib/status";
import type { NodeStatus, SkillGraph } from "../lib/types";

// Pure derivation unit tests — no page navigation. SkillEdge convention: source = prerequisite,
// target = the dependent skill (services/completion.py:30-31).
//
// The phantom-edge case: an LLM adaptation can emit an edge whose SOURCE names a node id that does
// not exist (the observed case: `mlops_tools`). The backend drops such edges in
// graph/skill_graph_adapt.prune_dangling_edges and again in services/completion.prereqs_by_node;
// lib/status.ts mirrors that invariant.
const node = (id: string, label: string) => ({ id, label, description: "" });
const state = (
  entries: Record<string, { status: NodeStatus; remediation_pending?: boolean }>,
) => entries;

test.describe("phantom-source edges", () => {
  // The observed regression: aria-label="Production ML Systems. Locked. Requires: Deep Learning
  // Foundations, mlops_tools, Feature Store & Data Pipelines" — the raw slug of a node that never
  // existed, read out to a screen reader as if it were a skill.
  test("a phantom prerequisite never reaches the lock message", () => {
    const graph: SkillGraph = {
      nodes: [
        node("deep_learning", "Deep Learning Foundations"),
        node("feature_store", "Feature Store & Data Pipelines"),
        node("prod_ml", "Production ML Systems"),
      ],
      edges: [
        { source: "deep_learning", target: "prod_ml" },
        { source: "mlops_tools", target: "prod_ml" }, // phantom: no such node
        { source: "feature_store", target: "prod_ml" },
      ],
    };
    const nodeState = state({
      deep_learning: { status: "available" },
      feature_store: { status: "available" },
      prod_ml: { status: "available" },
    });

    // Exactly the two real, incomplete prerequisites — asserting only "no mlops_tools" would also
    // pass on an over-aggressive filter that dropped the genuine ones.
    expect(incompletePrereqLabels(graph, nodeState, "prod_ml")).toEqual([
      "Deep Learning Foundations",
      "Feature Store & Data Pipelines",
    ]);
    // Still locked — the real prerequisites are genuinely unmet.
    expect(lockedNodeIds(graph, nodeState).has("prod_ml")).toBe(true);
  });

  test("a node whose only prerequisite was a phantom is available, not locked", () => {
    const graph: SkillGraph = {
      nodes: [node("prod_ml", "Production ML Systems")],
      edges: [{ source: "mlops_tools", target: "prod_ml" }],
    };
    const nodeState = state({ prod_ml: { status: "available" } });

    expect(lockedNodeIds(graph, nodeState).has("prod_ml")).toBe(false);
    expect(displayStatus(graph, nodeState).prod_ml).toBe("available");
    expect(incompletePrereqLabels(graph, nodeState, "prod_ml")).toEqual([]);
  });

  test("a mastered node gated only by a phantom counts as complete", () => {
    // The "stored broken session is finishable again" property: without the guard the phantom is an
    // unmasterable prerequisite, so this node could never be complete and the session never ends.
    const graph: SkillGraph = {
      nodes: [node("prod_ml", "Production ML Systems")],
      edges: [{ source: "mlops_tools", target: "prod_ml" }],
    };
    const nodeState = state({ prod_ml: { status: "mastered" } });

    expect(completeNodeIds(graph, nodeState).has("prod_ml")).toBe(true);
    expect(displayStatus(graph, nodeState).prod_ml).toBe("mastered");
  });

  test("real prerequisites still gate normally", () => {
    const graph: SkillGraph = {
      nodes: [node("basics", "Python Basics"), node("advanced", "Advanced Python")],
      edges: [{ source: "basics", target: "advanced" }],
    };

    const unmet = state({ basics: { status: "available" }, advanced: { status: "mastered" } });
    expect(lockedNodeIds(graph, unmet).has("advanced")).toBe(true);
    expect(incompletePrereqLabels(graph, unmet, "advanced")).toEqual(["Python Basics"]);

    const met = state({ basics: { status: "mastered" }, advanced: { status: "mastered" } });
    expect(lockedNodeIds(graph, met).has("advanced")).toBe(false);
    expect(completeNodeIds(graph, met).has("advanced")).toBe(true);
  });
});
