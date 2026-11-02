import { test, expect, type Page } from "@playwright/test";

test.setTimeout(45_000);

function fixture() {
  return {
    skill_graph: { summary: "Closure path", nodes: [
      { id: "a", label: "Skill A", description: "Double input", level: "foundational" },
      { id: "b", label: "Skill B", description: "Next skill", level: "foundational" },
    ], edges: [] },
    node_state: { a: { status: "available", best_score: 0, attempts: 0 }, b: { status: "available", best_score: 0, attempts: 0 } },
    selected_node: "a", events: [], lessons: { a: { content: "# Skill A\nPractice doubling.", exercise: {
      id: "e1", revision: "r1", language: "python", format: "coding_challenge", statement: "Double input.",
    } } },
  };
}

async function setup(page: Page, diagnostic = false) {
  const state: any = fixture();
  const requests: any[] = [];
  if (diagnostic) state.assessment = { submitted: false, status: "pending", quiz_revision: "q1", quiz: { questions: [
    { id: "q1", node_id: "a", question: "What is twice two?", options: ["1", "2", "3", "4"] },
  ] } };
  await page.addInitScript(() => { if (!localStorage.getItem("mindmorph.userId")) localStorage.setItem("mindmorph.userId", "closure@test.com"); });
  await page.route("**/vs/**", (route) => route.abort());
  await page.route("**://localhost:8000/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const body = method === "POST" ? route.request().postDataJSON() : {};
    if (method === "POST") requests.push({ path, body });
    if (path.endsWith("/assessment")) {
      state.assessment.status = body.action === "skip" ? "skipped" : "pending";
    }
    if (path.endsWith("/lessons/b")) return route.fulfill({ status: 503, json: { detail: "Optional lesson unavailable" } });
    if (path.endsWith("/grade")) return route.fulfill({ json: { session_id: "s1", learning_session: state,
      grade_result: { status: "platform_error", score: null, mastery_eligible: false, feedback: "Retry safely." } } });
    if (path.endsWith("/run")) return route.fulfill({ json: { run_id: body.run_id, status: "unavailable", message: "Runtime is offline." } });
    if (path.includes("/sessions/")) return route.fulfill({ json: { session_id: "s1", learning_session: state } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/session/s1");
  return { state, requests };
}

async function plain(page: Page) {
  const input = page.locator('textarea[aria-label="Your solution"]');
  const button = page.getByRole("button", { name: "Use plain text now" });
  await expect(button.or(input)).toBeVisible();
  if (await button.isVisible()) await button.click();
  await expect(input).toBeVisible();
  return input;
}

test("skip is persisted separately and can explicitly resume without a fake zero", async ({ page }) => {
  const { requests } = await setup(page, true);
  await page.getByRole("button", { name: /Skip/ }).click();
  await expect(page.getByText("Diagnostic skipped", { exact: true })).toBeVisible();
  expect(requests.at(-1).body.action).toBe("skip");
  expect(requests.at(-1).body.answers).toBeUndefined();
  await expect(page.getByText("0%", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Take diagnostic now" }).click();
  await expect(page.getByRole("heading", { name: "Find your starting point" })).toBeVisible();
});

test("ungraded result and refresh retain the response without counting a failed attempt", async ({ page }) => {
  const { requests } = await setup(page);
  await (await plain(page)).fill("print(42)");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await expect(page.getByText("Assessment service is unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("0%", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /Return to saved exercise/ }).click();
  await expect(await plain(page)).toHaveValue("print(42)");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await expect(page.getByText("Assessment service is unavailable", { exact: true })).toBeVisible();
  const submissions = requests.filter(r => r.path.endsWith("/grade"));
  expect(submissions[0].body.attempt_id).not.toBe(submissions[1].body.attempt_id);
  await page.reload();
  await expect(await plain(page)).toHaveValue("print(42)");
  await expect(page.getByText("0/2", { exact: true })).toBeVisible();
});

test("a saved result after a conflict can be recovered without submitting again", async ({ page }) => {
  const { state, requests } = await setup(page);
  const result = { status: "graded", method: "execution", score: 100, mastery_eligible: true, applied: false };
  let grades = 0;
  let applications = 0;
  await page.route("**/grade?*", async route => {
    grades += 1;
    await route.fulfill({ status: 409, json: { detail: "Saved progress changed. Your result is retained." } });
  });
  await page.route("**/attempts/**", async route => {
    if (route.request().method() === "POST") {
      applications += 1;
      state.node_state.a.attempts = 1;
      return route.fulfill({ json: { session_id: "s1", learning_session: state, grade_result: { ...result, applied: true } } });
    }
    return route.fulfill({ json: result });
  });
  await (await plain(page)).fill("print(42)");
  await page.getByRole("button", { name: "Grade my submission" }).click();
  await page.getByRole("button", { name: "Recover saved assessment" }).click();
  await expect(page.getByText("100%", { exact: true }).first()).toBeVisible();
  expect(grades).toBe(1);
  expect(applications).toBe(1);
});

test("new revision requires explicit restore and another learner gets no draft", async ({ page }) => {
  const { state } = await setup(page);
  await (await plain(page)).fill("old revision answer");
  state.lessons.a.exercise.revision = "r2";
  await page.reload();
  await expect(await plain(page)).toHaveValue("");
  await page.getByRole("button", { name: "Restore it", exact: true }).click();
  await expect(await plain(page)).toHaveValue("old revision answer");
  await page.evaluate(() => localStorage.setItem("mindmorph.userId", "another@test.com"));
  await page.reload();
  await expect(await plain(page)).toHaveValue("");
});

test("requesting new practice preserves the lesson and offers the earlier draft explicitly", async ({ page }) => {
  const { state } = await setup(page);
  await (await plain(page)).fill('older answer');
  let requests = 0;
  await page.route('**/lessons/a/practice', async route => {
    requests += 1;
    const body = route.request().postDataJSON();
    expect(body.exercise_revision).toBe('r1');
    expect(body.request_id).toBeTruthy();
    state.lessons.a.exercise = { ...state.lessons.a.exercise, id: 'e2', revision: 'r2', statement: 'A new validated task' };
    await route.fulfill({ json: { session_id: 's1', learning_session: state } });
  });
  await page.getByRole('button', { name: 'New practice task', exact: true }).click();
  await expect(page.getByText('A new validated task', { exact: true })).toBeVisible();
  await expect(page.getByText('Practice doubling.', { exact: false })).toBeVisible();
  await expect(await plain(page)).toHaveValue('');
  await page.getByRole('button', { name: 'Restore it', exact: true }).click();
  await expect(await plain(page)).toHaveValue('older answer');
  expect(requests).toBe(1);
});

test("Run uses a new binding for edited code and never submits a grade", async ({ page }) => {
  const { requests } = await setup(page);
  await (await plain(page)).fill("print(1)");
  await page.getByRole("button", { name: "Run solution" }).click();
  await expect(page.getByText("Runtime is offline.")).toBeVisible();
  await (await plain(page)).fill("print(2)");
  await page.getByRole("button", { name: "Run solution" }).click();
  await expect.poll(() => requests.filter((r) => r.path.endsWith("/run")).length).toBe(2);
  const runs = requests.filter((r) => r.path.endsWith("/run"));
  expect(runs[0].body.run_id).not.toBe(runs[1].body.run_id);
  expect(requests.some((r) => r.path.endsWith("/grade"))).toBe(false);
});

test("Run preserves sample input and changing only input creates a fresh binding", async ({ page }) => {
  const { requests } = await setup(page);
  await (await plain(page)).fill("print(int(input())*2)");
  await page.getByRole("textbox", { name: "Program input", exact: true }).fill("21");
  await page.getByRole("button", { name: "Run solution" }).click();
  await expect(page.getByText("Runtime is offline.")).toBeVisible();
  await page.getByRole("textbox", { name: "Program input", exact: true }).fill("22");
  await page.getByRole("button", { name: "Run solution" }).click();
  await expect.poll(() => requests.filter(r => r.path.endsWith('/run')).length).toBe(2);
  const runs = requests.filter(r => r.path.endsWith('/run'));
  expect(runs.map(r => r.body.stdin)).toEqual(['21', '22']);
  expect(runs[0].body.run_id).not.toBe(runs[1].body.run_id);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Program input", exact: true })).toHaveValue('22');
});

test("failed open retains draft even when browser storage is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("mindmorph.draft.")) throw new DOMException("Storage unavailable");
      return set.call(this, key, value);
    };
  });
  await setup(page);
  await (await plain(page)).fill("keep in memory");
  await page.getByRole("button", { name: /Skill B.*Available/ }).click();
  await expect(page.getByText("Optional lesson unavailable")).toBeVisible();
  await expect(await plain(page)).toHaveValue("keep in memory");
  await expect(page.getByText(/Browser storage is unavailable/)).toBeVisible();
});
