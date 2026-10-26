import dagre from "@dagrejs/dagre";
import type { SkillEdge, SkillNode } from "./types";

// The rendered skill card (SkillFlowNode): Tailwind w-64 = 256px; the label wraps to at most two
// lines (line-clamp-2) above the status row, so the tallest card is ~85px. Dagre needs the node box
// to reserve space and avoid overlap — keep these in sync with the card's classes or nodes collide.
export const NODE_WIDTH = 256;
export const NODE_HEIGHT = 88;

// Spacing tuned to the prior 280px column / 120px row feel of the hand-rolled layout.
const RANK_SEP = 110; // gap between prereq depth layers (horizontal, LR)
const NODE_SEP = 48; // gap between sibling nodes within a layer

export type Position = { x: number; y: number };

// ── Camera framing for the rewire cue ────────────────────────────────────────
// Fixed pixel margins, not React Flow's ratio padding. A ratio scales the margin with the frame
// (0.45 ate 173px per side of a 1118px container, ~31% of each axis) which lowers the bbox size at
// which the minZoom clamp engages — the opposite of what a wide graph needs.
// Y is the binding axis: the container is 420px tall against a 136px row pitch (88px card + 48px
// nodesep), so vertical room is what decides how much context fits, and every pixel spent on the
// vertical margin costs framed rows.
export const FOCUS_PADDING_X = 64;
export const FOCUS_PADDING_Y = 28;
// Headroom above React Flow's minZoom (0.5), and the whole point of the framing rule. Once the
// natural fit drops under minZoom the clamp holds zoom at 0.5 and `getViewportForBounds` throws the
// requested padding away, so the frame loses its entire margin budget and cards just outside the
// focus box get sliced by the container edge. Keeping the fit above this floor means the clamp never
// engages and the margin is always really applied. It is deliberately NOT a readability threshold:
// 0.6 renders a 14px label at 8.4px, small but no smaller than the whole-graph view this page
// already fits at mount. Raise it if a browser pass says the cue is unreadable — the cost is context
// (fewer cards framed), never a clipped frame.
export const FOCUS_MIN_ZOOM = 0.6;
// React Flow's own `minZoom` default, which the cue's `fitView` is clamped by. Below it the clamp
// engages and the padding above is discarded, which is what slices cards at the container edge —
// so it is the floor at which a frame is still guaranteed whole. It is deliberately LOWER than
// FOCUS_MIN_ZOOM: the focus subset is a chosen crop and owes the learner legibility, while framing
// the WHOLE graph is the same view this page already mounts at (`fitView` with the same clamp), so
// the only question there is whether anything gets cut. Keep in sync with <ReactFlow>'s minZoom.
export const REACT_FLOW_MIN_ZOOM = 0.5;

export type NodeBox = { x: number; y: number; width: number; height: number };

function boundsOf(ids: Iterable<string>, boxes: Record<string, NodeBox>) {
  let x1 = Infinity;
  let y1 = Infinity;
  let x2 = -Infinity;
  let y2 = -Infinity;
  for (const id of ids) {
    const b = boxes[id];
    if (!b) continue;
    x1 = Math.min(x1, b.x);
    y1 = Math.min(y1, b.y);
    x2 = Math.max(x2, b.x + b.width);
    y2 = Math.max(y2, b.y + b.height);
  }
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

/** The zoom `fitView` would choose for `ids`, before any min/max clamp. */
function naturalFitZoom(
  ids: Iterable<string>,
  boxes: Record<string, NodeBox>,
  viewport: { width: number; height: number },
): number {
  const { width, height } = boundsOf(ids, boxes);
  // Degenerate (no known boxes): report an unfittable frame so the caller stops widening rather
  // than treating "unmeasurable" as "fits".
  if (!(width > 0) || !(height > 0)) return 0;
  return Math.min(
    (viewport.width - FOCUS_PADDING_X * 2) / width,
    (viewport.height - FOCUS_PADDING_Y * 2) / height,
  );
}

/**
 * Which nodes the rewire camera should frame.
 *
 * If the whole graph fits, that's the answer — see the first branch. Everything below is the case
 * where it doesn't and a subset has to be chosen.
 *
 * A fixed hop count can't hold across graph sizes: two hops off a mid-graph node is five cards on a
 * young path, but on a mature one — where every high score has added an unlock edge reaching several
 * ranks downstream — it sweeps most of the map, the natural fit falls under minZoom, and the frame
 * clips. So take the first hop unconditionally (the new nodes plus what they attach to — the minimum
 * that reads as a connected change, and bounded because a remedial node has exactly one edge), then
 * keep widening only while the set still fits at `FOCUS_MIN_ZOOM`. Small graphs get more context, big
 * ones less, and the frame is legible either way.
 *
 * The last hop is taken PARTIALLY rather than dropped, because a whole hop on a dense graph is a jump
 * from three cards to an entire rank — refusing it outright would swap one cliff for another and land
 * the mature-graph case back on the three-cards framing the fixed two hops were written to avoid.
 */
export function focusFrameIds(
  seedIds: string[],
  edges: SkillEdge[],
  boxes: Record<string, NodeBox>,
  viewport: { width: number; height: number },
): string[] {
  // Framing a subset only ever makes sense when the whole graph WON'T fit. When it will, cropping is
  // strictly worse: it can strand a card outside the frame that the learner could otherwise see, and
  // an unreachable one (an island left by a pruned edge) is stranded at ANY zoom because the widen
  // below only travels edges. Checked against every measured box, not the reachable set, so both the
  // too-small-to-widen case and the island case are covered. The floor is the clamp floor, not the
  // legibility floor — see REACT_FLOW_MIN_ZOOM.
  const allIds = Object.keys(boxes);
  if (naturalFitZoom(allIds, boxes, viewport) >= REACT_FLOW_MIN_ZOOM) return allIds;

  const ids = new Set(seedIds);
  const widen = () => {
    const frontier = new Set(ids);
    for (const e of edges) {
      if (frontier.has(e.source)) ids.add(e.target);
      if (frontier.has(e.target)) ids.add(e.source);
    }
  };

  widen(); // first hop is unconditional
  // Then hop until the graph saturates or the next hop would overflow the frame.
  for (;;) {
    const before = new Set(ids);
    widen();
    if (ids.size === before.size) break;
    if (naturalFitZoom(ids, boxes, viewport) < FOCUS_MIN_ZOOM) {
      // A whole hop is all-or-nothing, and on a dense graph one hop is a jump from three cards to a
      // whole rank — dropping it wholesale is a cliff, not a gradient. So take as much of the
      // rejected hop as fits: nearest to what's already framed first, since those are the cards that
      // read as connected to the change and the ones that would otherwise be cut by the frame edge.
      const centre = centreOf(before, boxes);
      const rejected = [...ids]
        .filter((id) => !before.has(id))
        .sort((a, b) => distance(a, centre, boxes) - distance(b, centre, boxes));
      const kept = new Set(before);
      for (const id of rejected) {
        kept.add(id);
        if (naturalFitZoom(kept, boxes, viewport) < FOCUS_MIN_ZOOM) {
          kept.delete(id); // a farther card may still fit if it sits inside the current extent
        }
      }
      return [...kept];
    }
  }
  return [...ids];
}

function centreOf(ids: Iterable<string>, boxes: Record<string, NodeBox>) {
  const { x, y, width, height } = boundsOf(ids, boxes);
  return { x: x + width / 2, y: y + height / 2 };
}

function distance(id: string, centre: { x: number; y: number }, boxes: Record<string, NodeBox>) {
  const b = boxes[id];
  if (!b) return Infinity;
  return Math.hypot(b.x + b.width / 2 - centre.x, b.y + b.height / 2 - centre.y);
}

/**
 * Edge-aware layered layout for the prerequisite DAG.
 *
 * SkillEdge convention (services/completion.py:30-31): source = prerequisite, target = the skill
 * that depends on it. With `rankdir: "LR"` dagre places sources left of targets, so prerequisites —
 * including remedial nodes added mid-session — render to the LEFT of the node they unlock, and the
 * whole graph reflows automatically when nodes are appended. Returns ReactFlow top-left positions
 * (dagre reports node centers, so we offset by half the node box).
 */
export function layoutSkillGraph(
  nodes: SkillNode[],
  edges: SkillEdge[],
): { positions: Record<string, Position> } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", ranksep: RANK_SEP, nodesep: NODE_SEP });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  // Only wire edges whose endpoints exist, so a malformed/dangling edge can't crash layout.
  const ids = new Set(nodes.map((n) => n.id));
  for (const e of edges) {
    if (ids.has(e.source) && ids.has(e.target)) {
      g.setEdge(e.source, e.target);
    }
  }

  dagre.layout(g);

  const positions: Record<string, Position> = {};
  for (const n of nodes) {
    const node = g.node(n.id);
    positions[n.id] = node
      ? { x: node.x - NODE_WIDTH / 2, y: node.y - NODE_HEIGHT / 2 }
      : { x: 0, y: 0 };
  }
  return { positions };
}
