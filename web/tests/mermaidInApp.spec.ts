import { type Page, expect, test } from "@playwright/test";
import { sanitizeMermaid } from "../lib/mermaid";

// In-situ counterpart to mermaidContainment.spec.ts. That spec drives the mermaid bundle directly in
// a blank page, so it proves mermaid's behaviour under the flags — not the app's. This one renders a
// genuinely unrepairable diagram through the real lesson panel and asserts what a learner would see:
// the <pre> fallback carrying the ORIGINAL source, and nothing orphaned onto document.body —
// including after the diagram unmounts.
//
// The diagram is the case the sanitiser deliberately refuses (a round node whose label carries
// parens): quoting it would risk flattening a valid shape, so containment is what has to hold.
const UNREPAIRABLE = "graph TD\n  A((Raw Files (CSV, JSON, SQL))) --> B[Parse]";

const EXERCISE = {
  format: "coding_challenge",
  statement: "Write `load()`.",
  grading_artifact: { format: "coding_challenge", unit_tests: [] },
};

/** Two unlinked nodes: 'a' carries the diagram, 'b' is a plain lesson used to mount/unmount it. */
async function mockApi(page: Page, diagram: string) {
  const ls = {
    skill_graph: {
      summary: "Learn Data",
      nodes: [
        { id: "a", label: "Pipelines", description: "ingest", level: "foundational" },
        { id: "b", label: "Storage", description: "warehouse", level: "foundational" },
      ],
      edges: [] as unknown[],
    },
    summary: "Learn Data",
    node_state: {
      a: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null },
      b: { status: "available", best_score: 0, attempts: 0, weaknesses: [], last_feedback: null },
    },
    lessons: {} as Record<string, unknown>,
    selected_node: null as string | null,
    format_type: "B",
  };
  const sid = "sess1";

  await page.route("**/sessions/*", (route) => route.fulfill({ json: [] }));
  await page.route(`**/sessions/*/${sid}`, (route) =>
    route.fulfill({ json: { session_id: sid, learning_session: ls } }),
  );
  await page.route(`**/sessions/*/${sid}/lessons/a`, (route) => {
    ls.lessons.a = {
      content: `# Pipelines\nHere is the shape of it.\n\n\`\`\`mermaid\n${diagram}\n\`\`\`\n`,
      exercise: EXERCISE,
    };
    ls.selected_node = "a";
    return route.fulfill({ json: { session_id: sid, learning_session: ls } });
  });
  await page.route(`**/sessions/*/${sid}/lessons/b`, (route) => {
    ls.lessons.b = { content: "# Storage\nNo diagram here.", exercise: EXERCISE };
    ls.selected_node = "b";
    return route.fulfill({ json: { session_id: sid, learning_session: ls } });
  });
}

async function openSession(page: Page) {
  await page.addInitScript(() => localStorage.setItem("mindmorph.userId", "e2e@test.com"));
  await page.goto("/session/sess1");
  await expect(page.getByText("skills complete")).toBeVisible();
}

async function openLesson(page: Page, label: string, heading: string) {
  await page.getByText(label).first().click();
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

/** Everything mermaid could have orphaned outside the React tree, plus its error graphic. */
function domState(page: Page) {
  return page.evaluate(() => {
    // Mermaid's fingerprint on a body-level node: the render-id-derived ids it appends (`mmd-` for
    // the working div, `d`/`i` prefixed variants), its error bomb graphic, or its error text. Only
    // these make a body child a mermaid artifact. Monaco (`.context-view`,
    // `.monaco-aria-container`) and the Next dev overlay also append to body, at times this test
    // cannot control, so a whole-body snapshot comparison would be asserting on their timing rather
    // than on mermaid. Matched on the body child ITSELF, not its subtree: a successful render's svg
    // also carries an `mmd-` id, but it lives inside <main> in the React tree and is the product,
    // not an artifact. Mermaid's temp/error div is always a direct body child ided off the render id
    // (`mmd-…`, `dmmd-…`, `immd-…`), which is exactly what the original bug orphaned.
    const MERMAID_SELECTOR =
      '[id^="mmd-"], [id^="dmmd-"], [id^="immd-"], [aria-roledescription="error"], .error-icon, .error-text';
    return {
      // Descriptors of the body children that carry mermaid's fingerprint. Empty is the property
      // under test: mermaid leaves nothing attached to document.body.
      mermaidBodyChildren: [...document.body.children]
        .filter((c) => c.matches(MERMAID_SELECTOR))
        .map((c) => `${c.tagName}#${c.id}.${c.className}`),
      // Scoped to body's DIRECT children: that is where mermaid appends its working div when render()
      // is called without an svgContainingElement. A successfully rendered diagram also carries
      // mmd-prefixed ids, but those live inside the React tree and are not artifacts.
      orphanedRenderDivs: document.querySelectorAll(
        'body > [id^="mmd-"], body > [id^="dmmd-"], body > [id^="immd-"]',
      ).length,
      errorGraphics: document.querySelectorAll(
        '[aria-roledescription="error"], .error-icon, .error-text',
      ).length,
      syntaxErrorText: document.body.innerText.includes("Syntax error"),
    };
  });
}

test("an unrepairable diagram falls back to the original source and orphans nothing", async ({
  page,
}) => {
  // Pre-flight: this really is a diagram the sanitiser refuses to touch, so the failure branch is
  // reached honestly rather than by feeding mermaid something the app would have repaired.
  expect(sanitizeMermaid(UNREPAIRABLE)).toBe(UNREPAIRABLE);

  await mockApi(page, UNREPAIRABLE);
  await openSession(page);

  // Open a plain lesson first, so the diagram lesson is reached by a real mount/unmount cycle.
  await openLesson(page, "Storage", "Storage");

  await openLesson(page, "Pipelines", "Pipelines");
  const fallback = page.locator("pre.overflow-x-auto", { hasText: "Raw Files (CSV, JSON, SQL)" });
  await expect(fallback).toBeVisible();
  // The original source, unquoted parens and all — the `code` prop is never sanitised.
  await expect(fallback).toContainText("A((Raw Files (CSV, JSON, SQL)))");
  await expect(page.getByText("Rendering diagram…")).toHaveCount(0);
  await expect(page.locator(".lesson-prose svg")).toHaveCount(0);

  const rendered = await domState(page);
  expect(rendered.mermaidBodyChildren).toEqual([]);
  expect(rendered.orphanedRenderDivs).toBe(0);
  expect(rendered.errorGraphics).toBe(0);
  expect(rendered.syntaxErrorText).toBe(false);

  // Unmount: the pre-fix artifact lived outside the React tree and outlived exactly this.
  await openLesson(page, "Storage", "Storage");
  await expect(page.locator("pre", { hasText: "Raw Files" })).toHaveCount(0);

  const unmounted = await domState(page);
  expect(unmounted.mermaidBodyChildren).toEqual([]);
  expect(unmounted.orphanedRenderDivs).toBe(0);
  expect(unmounted.errorGraphics).toBe(0);
  expect(unmounted.syntaxErrorText).toBe(false);
});

// The companion test below used to be impossible under `next dev`: React StrictMode double-invokes
// the effect, and while both passes shared one useId-derived renderId the discarded first pass's
// `finally { removeMermaidArtifacts(renderId) }` deleted the second pass's in-flight working div, so
// BOTH renders threw and every diagram fell back to <pre> in dev. The passes are now distinguished
// by a per-invocation sequence suffix on the render id (`mmd-<useId>-<n>`), so each pass owns — and
// cleans up — only its own mermaid nodes. The test therefore asserts BOTH properties in dev: the
// diagram renders an <svg>, and the successful render still orphans nothing onto document.body,
// including after unmount.
const VALID = "graph TD\n  A[Alpha] --> B[Beta]";

test("a valid diagram renders an svg and still orphans nothing", async ({ page }) => {
  await mockApi(page, VALID);
  await openSession(page);

  await openLesson(page, "Storage", "Storage");

  await openLesson(page, "Pipelines", "Pipelines");
  const svg = page.locator(".lesson-prose svg");
  await expect(svg).toBeVisible();
  await expect(svg).toHaveAttribute("id", /^mmd-/);
  await expect(page.getByText("Rendering diagram…")).toHaveCount(0);
  await expect(page.locator("pre", { hasText: "graph TD" })).toHaveCount(0);

  // A successful render also appends a working div to document.body while it runs; it must be gone.
  const rendered = await domState(page);
  expect(rendered.mermaidBodyChildren).toEqual([]);
  expect(rendered.orphanedRenderDivs).toBe(0);

  await openLesson(page, "Storage", "Storage");
  await expect(page.locator(".lesson-prose svg")).toHaveCount(0);
  const unmounted = await domState(page);
  expect(unmounted.mermaidBodyChildren).toEqual([]);
  expect(unmounted.orphanedRenderDivs).toBe(0);
});
