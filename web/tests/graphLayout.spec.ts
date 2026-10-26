import { expect, test } from "@playwright/test";
import {
  FOCUS_MIN_ZOOM,
  FOCUS_PADDING_X,
  FOCUS_PADDING_Y,
  focusFrameIds,
  layoutSkillGraph,
  NODE_HEIGHT,
  NODE_WIDTH,
  type NodeBox,
  REACT_FLOW_MIN_ZOOM,
} from "../lib/graphLayout";
import type { SkillEdge, SkillNode } from "../lib/types";

// Pure layout unit tests — no page navigation. SkillEdge convention: source = prerequisite,
// target = the dependent skill (services/completion.py:30-31).
const node = (id: string): SkillNode => ({ id, label: id, description: "" });

test.describe("layoutSkillGraph", () => {
  test("places prerequisites left of dependents in a chain A->B->C", () => {
    const nodes: SkillNode[] = [node("A"), node("B"), node("C")];
    const edges: SkillEdge[] = [
      { source: "A", target: "B" },
      { source: "B", target: "C" },
    ];

    const { positions } = layoutSkillGraph(nodes, edges);

    expect(positions.A.x).toBeLessThan(positions.B.x);
    expect(positions.B.x).toBeLessThan(positions.C.x);
  });

  test("no two nodes share an identical position", () => {
    const nodes: SkillNode[] = [node("A"), node("B"), node("C"), node("D")];
    const edges: SkillEdge[] = [
      { source: "A", target: "C" },
      { source: "B", target: "C" },
      { source: "C", target: "D" },
    ];

    const { positions } = layoutSkillGraph(nodes, edges);
    const seen = new Set(Object.values(positions).map((p) => `${p.x},${p.y}`));

    expect(seen.size).toBe(nodes.length);
  });

  test("ignores edges with dangling endpoints without crashing", () => {
    const nodes: SkillNode[] = [node("A"), node("B")];
    const edges: SkillEdge[] = [
      { source: "A", target: "B" },
      { source: "A", target: "ghost" },
    ];

    const { positions } = layoutSkillGraph(nodes, edges);

    expect(Object.keys(positions).sort()).toEqual(["A", "B"]);
    expect(positions.A.x).toBeLessThan(positions.B.x);
  });
});

// ── Camera framing ───────────────────────────────────────────────────────────
// The rewire cue regressed once already: values tuned on a small graph clipped on a bigger one, and
// nothing caught it because the arithmetic was inline in an effect. It is pure arithmetic over the
// dagre layout, so it is guarded here rather than in a browser.
const CARD = { width: NODE_WIDTH, height: NODE_HEIGHT };
const VIEWPORT = { width: 1116, height: 418 }; // the h-[420px] surface, less its 1px border

function boxesFor(nodes: SkillNode[], edges: SkillEdge[]): Record<string, NodeBox> {
  const { positions } = layoutSkillGraph(nodes, edges);
  return Object.fromEntries(
    nodes.map((n) => [n.id, { ...positions[n.id], ...CARD }]),
  );
}

function fitZoom(ids: string[], boxes: Record<string, NodeBox>): number {
  const xs = ids.map((id) => boxes[id]);
  const width = Math.max(...xs.map((b) => b.x + b.width)) - Math.min(...xs.map((b) => b.x));
  const height = Math.max(...xs.map((b) => b.y + b.height)) - Math.min(...xs.map((b) => b.y));
  return Math.min(
    (VIEWPORT.width - FOCUS_PADDING_X * 2) / width,
    (VIEWPORT.height - FOCUS_PADDING_Y * 2) / height,
  );
}

/**
 * A path of `rankCount` ranks, `perRank` sibling skills each, fully connected rank-to-rank, plus
 * `unlocks` long-range edges from the middle node to nodes several ranks downstream — the shape a
 * mature graph reaches after the adaptation agent has added an unlock edge per high score. Then two
 * remedial prerequisites are hung off that middle node, which is the low-score adaptation.
 */
function rewiredGraph(rankCount: number, perRank: number, unlocks: number) {
  const ranks: string[][] = [];
  const nodes: SkillNode[] = [];
  const edges: SkillEdge[] = [];
  for (let r = 0; r < rankCount; r++) {
    const rank = Array.from({ length: perRank }, (_, j) => `n${r}_${j}`);
    for (const id of rank) nodes.push(node(id));
    if (r > 0) for (const a of ranks[r - 1]) for (const b of rank) edges.push({ source: a, target: b });
    ranks.push(rank);
  }
  const mid = Math.floor(rankCount / 2);
  const graded = ranks[mid][0];
  for (let u = 0; u < unlocks; u++) {
    const target = ranks[Math.min(mid + 3 + u, rankCount - 1)][u % perRank];
    if (target !== graded) edges.push({ source: graded, target });
  }
  for (const id of ["rem1", "rem2"]) {
    nodes.push(node(id));
    edges.push({ source: id, target: graded });
  }
  return { nodes, edges, seeds: ["rem1", "rem2"] };
}

/**
 * The world-space rectangle the container actually shows after the cue's `fitView` — a port of
 * `getViewportForBounds` (@xyflow/system) for the options SkillGraph passes: pixel padding, the
 * minZoom/maxZoom clamp, and a frame centred on the framed bounds. Asserting containment against
 * this, rather than against the framed-id list, is what catches "the node was excluded and really
 * did land outside the glass" as opposed to merely "the set was smaller than the graph".
 */
function framedWorldRect(ids: string[], boxes: Record<string, NodeBox>) {
  const bs = ids.map((id) => boxes[id]);
  const x1 = Math.min(...bs.map((b) => b.x));
  const y1 = Math.min(...bs.map((b) => b.y));
  const x2 = Math.max(...bs.map((b) => b.x + b.width));
  const y2 = Math.max(...bs.map((b) => b.y + b.height));
  const zoom = Math.min(Math.max(fitZoom(ids, boxes), REACT_FLOW_MIN_ZOOM), 1.1);
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  return {
    left: cx - VIEWPORT.width / 2 / zoom,
    right: cx + VIEWPORT.width / 2 / zoom,
    top: cy - VIEWPORT.height / 2 / zoom,
    bottom: cy + VIEWPORT.height / 2 / zoom,
  };
}

test.describe("focusFrameIds", () => {
  test("always frames the new nodes and what they attach to", () => {
    const { nodes, edges, seeds } = rewiredGraph(7, 3, 3);
    const boxes = boxesFor(nodes, edges);

    const ids = focusFrameIds(seeds, edges, boxes, VIEWPORT);

    expect(ids).toEqual(expect.arrayContaining([...seeds, "n3_0"]));
  });

  test("frames a young graph with more context than a mature one", () => {
    const young = rewiredGraph(3, 2, 0);
    const mature = rewiredGraph(8, 3, 3);

    const youngIds = focusFrameIds(young.seeds, young.edges, boxesFor(young.nodes, young.edges), VIEWPORT);
    const matureIds = focusFrameIds(mature.seeds, mature.edges, boxesFor(mature.nodes, mature.edges), VIEWPORT);

    // The young path is small enough to frame whole — the cue shows the map, not three cards adrift.
    expect(youngIds.length).toBe(young.nodes.length);
    // The mature one is not, so it gives up context rather than the frame — but it degrades to a
    // partial hop, not back to the three cards a whole-hop-or-nothing rule would leave.
    expect(matureIds.length).toBeLessThan(mature.nodes.length);
    expect(matureIds.length).toBeGreaterThan([...mature.seeds, "n4_0"].length);
  });

  test("strands no node on a graph small enough to frame whole", () => {
    // The reported failure: the cue framed a subset and left a card fully outside the right edge,
    // on a graph Fit View showed complete. Cropping is only ever worth a stranded card when the
    // whole graph genuinely will not fit.
    for (const [rankCount, perRank, unlocks] of [
      [3, 2, 0],
      [4, 2, 0],
      [5, 2, 2], // wholeFit 0.574 — the [minZoom, FOCUS_MIN_ZOOM) band the old rule cropped
    ] as const) {
      const { nodes, edges, seeds } = rewiredGraph(rankCount, perRank, unlocks);
      const boxes = boxesFor(nodes, edges);
      const all = nodes.map((n) => n.id);
      // Precondition: these really are graphs the whole-graph branch should claim.
      expect(fitZoom(all, boxes)).toBeGreaterThanOrEqual(REACT_FLOW_MIN_ZOOM);

      const ids = focusFrameIds(seeds, edges, boxes, VIEWPORT);
      const frame = framedWorldRect(ids, boxes);

      const label = `${rankCount}x${perRank} ranks, ${unlocks} unlocks`;
      expect(ids.length, `${label}: framed ${ids.length}/${all.length}`).toBe(all.length);
      for (const id of all) {
        const b = boxes[id];
        expect(b.x, `${label}: ${id} off the left edge`).toBeGreaterThanOrEqual(frame.left);
        expect(b.x + b.width, `${label}: ${id} off the right edge`).toBeLessThanOrEqual(frame.right);
        expect(b.y, `${label}: ${id} off the top edge`).toBeGreaterThanOrEqual(frame.top);
        expect(b.y + b.height, `${label}: ${id} off the bottom edge`).toBeLessThanOrEqual(frame.bottom);
      }
    }
  });

  test("frames an unreachable node when the graph fits whole", () => {
    // `focusFrameIds` grows by walking edges, so a node no edge reaches — an island left by the
    // dangling-edge filter in SkillGraph — was excluded at ANY zoom, however roomy the frame. The
    // whole-graph branch keys on the measured boxes rather than the reachable set, which covers it.
    const { nodes, edges, seeds } = rewiredGraph(4, 2, 0);
    const withIsland = [...nodes, node("island")];
    const boxes = boxesFor(withIsland, edges);

    const ids = focusFrameIds(seeds, edges, boxes, VIEWPORT);

    expect(fitZoom(withIsland.map((n) => n.id), boxes)).toBeGreaterThanOrEqual(REACT_FLOW_MIN_ZOOM);
    expect(ids).toContain("island");
  });

  test("still crops, and crops partially, on a graph too big to frame whole", () => {
    // The large-graph behaviour is deliberate and must not regress: the frame gives up context
    // rather than legibility, but degrades to a partial hop instead of collapsing to the seeds.
    const { nodes, edges, seeds } = rewiredGraph(8, 3, 3);
    const boxes = boxesFor(nodes, edges);

    const ids = focusFrameIds(seeds, edges, boxes, VIEWPORT);

    expect(fitZoom(nodes.map((n) => n.id), boxes)).toBeLessThan(REACT_FLOW_MIN_ZOOM);
    expect(ids.length).toBeLessThan(nodes.length);
    expect(ids.length).toBeGreaterThan([...seeds, "n4_0"].length);
    expect(fitZoom(ids, boxes)).toBeGreaterThanOrEqual(FOCUS_MIN_ZOOM);
  });

  test("keeps the fit above the legibility floor at every graph size", () => {
    // The regression this guards: values that framed cleanly at 5 nodes clipped at 15. Sweep the
    // whole range the consensus agent produces (6-14 nodes) plus growth well past it.
    for (const rankCount of [2, 3, 4, 5, 6, 7, 8, 10]) {
      for (const perRank of [1, 2, 3]) {
        for (const unlocks of [0, 2, 3]) {
          const { nodes, edges, seeds } = rewiredGraph(rankCount, perRank, unlocks);
          const boxes = boxesFor(nodes, edges);

          const ids = focusFrameIds(seeds, edges, boxes, VIEWPORT);

          // Two floors, because the two frames answer to different things. A CROPPED frame is a
          // choice the cue made, so it owes legibility: FOCUS_MIN_ZOOM. A WHOLE-graph frame is the
          // same view the page mounts at, so it only owes not being clipped: the minZoom clamp,
          // below which fitView throws the padding away and slices edge cards. Holding the whole
          // -graph case to FOCUS_MIN_ZOOM would just forbid framing graphs that fit — the bug.
          const floor = ids.length === nodes.length ? REACT_FLOW_MIN_ZOOM : FOCUS_MIN_ZOOM;
          expect(
            fitZoom(ids, boxes),
            `${rankCount}x${perRank} ranks, ${unlocks} unlock edges: framed ${ids.length}/${nodes.length}`,
          ).toBeGreaterThanOrEqual(floor);
        }
      }
    }
  });
});
