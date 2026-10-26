import { type Page, expect, test } from "@playwright/test";

// The frontend half of the read-time phantom-prerequisite guard (e9694bb + 36a81c0). A session
// persisted before the build-time guard landed carries an edge whose SOURCE is a node id the graph
// never contained; as a prerequisite it can never be mastered, so its dependant stays locked forever
// and the raw slug leaks into the lock message. The backend now ignores it at read time; this drives
// the same session through the real UI and asserts the mirror in lib/status.ts and SkillGraph agree.
const PHANTOM = "mlops_tools";

function sessionWithPhantomSource() {
  return {
    skill_graph: {
      summary: "Learn ML",
      nodes: [
        { id: "a", label: "Python Basics", description: "vars", level: "foundational" },
        { id: "b", label: "Model Serving", description: "deploy", level: "intermediate" },
      ],
      // 'a' is a real prerequisite of 'b'; PHANTOM is not a node at all.
      edges: [
        { source: "a", target: "b", relation: "prerequisite" },
        { source: PHANTOM, target: "b", relation: "prerequisite" },
      ],
    },
    summary: "Learn ML",
    node_state: {
      a: { status: "mastered", best_score: 100, attempts: 1, weaknesses: [], last_feedback: null },
      b: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null },
    },
    lessons: {} as Record<string, unknown>,
    selected_node: null as string | null,
    format_type: "B",
  };
}

async function mockApi(page: Page) {
  const ls = sessionWithPhantomSource();
  const sid = "sess1";
  await page.route("**/sessions/*", (route) => route.fulfill({ json: [] }));
  await page.route(`**/sessions/*/${sid}`, (route) =>
    route.fulfill({ json: { session_id: sid, learning_session: ls } }),
  );
  await page.route(`**/sessions/*/${sid}/lessons/b`, (route) => {
    ls.lessons.b = {
      content: "# Model Serving\nShip it.",
      exercise: { format: "coding_challenge", statement: "s", grading_artifact: { format: "coding_challenge", unit_tests: [] } },
    };
    ls.selected_node = "b";
    return route.fulfill({ json: { session_id: sid, learning_session: ls } });
  });
}

test("a phantom prerequisite neither locks the node nor leaks its slug", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "e2e@test.com"));
  await page.goto("/session/sess1");
  await expect(page.getByText("skills complete")).toBeVisible();

  // 'a' is mastered and is b's only REAL prerequisite, so b must read as available, not locked.
  await expect(page.getByRole("button", { name: "Model Serving. Available" })).toBeVisible();
  await expect(page.getByText(new RegExp(PHANTOM))).toHaveCount(0);

  // Opening it must go through rather than surface a lock message naming the phantom.
  await page.getByText("Model Serving").first().click();
  await expect(page.getByRole("heading", { name: "Model Serving", level: 1 })).toBeVisible();
  await expect(page.getByText(/Locked — first complete/)).toHaveCount(0);
  await expect(page.getByText(new RegExp(PHANTOM))).toHaveCount(0);

  // React Flow is handed the pruned edge list: one real edge, no phantom-source edge.
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
});
