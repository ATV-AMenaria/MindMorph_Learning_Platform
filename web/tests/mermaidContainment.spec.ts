import path from "node:path";
import { expect, test } from "@playwright/test";
import { sanitizeMermaid } from "../lib/mermaid";

// Drives the real mermaid bundle in a blank page (no app server needed) to prove the two halves of
// the containment fix: sanitised source parses, and an unparseable diagram leaves nothing behind in
// document.body — the orphaned "Syntax error" bomb graphic that used to survive unmount.
const MERMAID_BUNDLE = path.join(__dirname, "..", "node_modules", "mermaid", "dist", "mermaid.min.js");
const BROKEN = "graph TD\n  A[Raw Files (CSV, JSON, SQL)] --> B[Parse]";

async function renderInPage(page: import("@playwright/test").Page, code: string) {
  await page.goto("about:blank");
  await page.addScriptTag({ path: MERMAID_BUNDLE });
  return page.evaluate(async (source) => {
    const mermaid = (window as unknown as { mermaid: typeof import("mermaid").default }).mermaid;
    mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      securityLevel: "strict",
      suppressErrorRendering: true,
    });
    let threw = false;
    try {
      await mermaid.render("mmd-test", source);
    } catch {
      threw = true;
    }
    return { threw, leftovers: document.body.innerHTML.trim() };
  }, code);
}

test("sanitised source parses instead of failing", async ({ page }) => {
  const { threw, leftovers } = await renderInPage(page, sanitizeMermaid(BROKEN));
  expect(threw).toBe(false);
  expect(leftovers).toBe("");
});

test("an unparseable diagram leaves no artifact in document.body", async ({ page }) => {
  const { threw, leftovers } = await renderInPage(page, "graph TD\n  A[[[ --> ]]]B");
  expect(threw).toBe(true);
  expect(leftovers).toBe("");
});
