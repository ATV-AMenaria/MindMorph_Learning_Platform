import { test, expect, type Page } from "@playwright/test";
import { seedUser } from "./auth-fixture";

type Mode = "foundation" | "standard" | "challenge";

async function setup(page: Page, status: "ready" | "insufficient_evidence" | "unavailable" | "disabled", mode: Mode = "foundation", locked = false) {
  const tracking = status === "disabled" ? undefined : {
    status, probability: status === "ready" ? (mode === "challenge" ? 0.972 : 0.4344) : null,
    observation_count: status === "insufficient_evidence" ? 1 : 3, model_version: "bkt-v1",
    recommendation: { mode, reason: status === "insufficient_evidence"
      ? "Complete more new exercises to build your learning estimate."
      : status === "unavailable" ? "Continue with standard practice."
      : mode === "foundation" ? "Strengthen this skill with a smaller problem and a worked example."
      : "Try applying this skill in a more challenging problem." },
  };
  const state = {
    skill_graph: { nodes: [{ id: "loops", label: "Python loops", description: "Repeat operations" }], edges: [] },
    node_state: { loops: { status: locked ? "needs_review" : "mastered", best_score: locked ? 25 : 100,
      attempts: 3, weaknesses: [], last_feedback: null, knowledge_tracking: tracking, remediation_pending: locked } },
    selected_node: "loops", events: [], lessons: { loops: { content: "# Python loops\nRepeat operations with a loop.",
      exercise: { id: "e1", revision: "r1", format: "case_study", statement: "Explain this loop." } } },
  };
  const requests: unknown[] = [];
  await seedUser(page, "bkt-demo@test.com");
  await page.route("**://localhost:8000/**", async route => {
    if (route.request().method() === "POST" && new URL(route.request().url()).pathname.endsWith("/practice")) {
      const body = route.request().postDataJSON();
      requests.push(body);
      state.lessons.loops.exercise = { ...state.lessons.loops.exercise, id: "e2", revision: "r2",
        statement: mode === "foundation" ? "Use repeated addition to calculate the total." : "Apply loops to calculate a shopping cart total." };
    }
    await route.fulfill({ json: { session_id: "bkt", learning_session: state } });
  });
  await page.goto("/session/bkt");
  return { requests };
}

test("foundation recommendation guides explicit new practice and survives reload", async ({ page }) => {
  const { requests } = await setup(page, "ready");
  const card = page.getByRole("complementary", { name: "Practice recommendation" });
  await expect(card.getByText("Strengthen foundations", { exact: true })).toBeVisible();
  await expect(card.getByText("Experimental understanding estimate: 43%", { exact: true })).toBeVisible();
  await expect(card.getByText("Based on 3 distinct exercises for this skill.", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Your response" }).fill("My saved response");
  await page.getByRole("button", { name: "New practice task", exact: true }).click();
  await expect(page.getByText("Use repeated addition to calculate the total.", { exact: true })).toBeVisible();
  expect(requests).toHaveLength(1);
  expect(requests[0]).not.toHaveProperty("practice_guidance"); // Server-owned, never supplied by the client.
  await expect(page.getByText("A previous exercise draft is saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(card.getByText("Experimental understanding estimate: 43%", { exact: true })).toBeVisible();
});

test("consistent learner receives application challenge", async ({ page }) => {
  await setup(page, "ready", "challenge");
  const card = page.getByRole("complementary", { name: "Practice recommendation" });
  await expect(card.getByText("Try an application challenge", { exact: true })).toBeVisible();
  await expect(card.getByText("Experimental understanding estimate: 97%", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New practice task", exact: true }).click();
  await expect(page.getByText("Apply loops to calculate a shopping cart total.", { exact: true })).toBeVisible();
});

test("insufficient and unavailable evidence never show a fabricated percentage", async ({ page }) => {
  await setup(page, "insufficient_evidence");
  let card = page.getByRole("complementary", { name: "Practice recommendation" });
  await expect(card.getByText("Building your learning estimate", { exact: true })).toBeVisible();
  await expect(card.getByText(/Experimental understanding estimate/)).toHaveCount(0);
  await page.unroute("**://localhost:8000/**");
  await setup(page, "unavailable");
  card = page.getByRole("complementary", { name: "Practice recommendation" });
  await expect(card.getByText("Recommendations temporarily unavailable", { exact: true })).toBeVisible();
  await expect(card.getByText(/Experimental understanding estimate/)).toHaveCount(0);
});

test("disabled feature leaves the existing practice flow visible", async ({ page }) => {
  await setup(page, "disabled");
  await expect(page.getByRole("complementary", { name: "Practice recommendation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New practice task", exact: true })).toBeVisible();
});

test("existing locks take priority over practice recommendations", async ({ page }) => {
  await setup(page, "ready", "foundation", true);
  await expect(page.getByRole("complementary", { name: "Practice recommendation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New practice task", exact: true })).toBeDisabled();
});
