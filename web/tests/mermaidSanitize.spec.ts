import { expect, test } from "@playwright/test";
import { sanitizeMermaid } from "../lib/mermaid";

// Pure string unit tests — no page navigation. The failing fence below is the one observed live:
// unquoted parentheses in a node label make mermaid throw, which used to orphan its error graphic
// at document-body level.
test.describe("sanitizeMermaid", () => {
  test("quotes a node label containing parentheses", () => {
    expect(sanitizeMermaid("graph TD\n  A[Raw Files (CSV, JSON, SQL)] --> B[Parse]")).toBe(
      'graph TD\n  A["Raw Files (CSV, JSON, SQL)"] --> B[Parse]',
    );
  });

  test("quotes every offending label on a line", () => {
    expect(sanitizeMermaid("A[Label (x)] --> B[Other (y)]")).toBe(
      'A["Label (x)"] --> B["Other (y)"]',
    );
  });

  // The real risk is mangling diagrams that already parse. These must round-trip untouched.
  for (const valid of [
    "graph TD\n  A[Plain label] --> B[Another]",
    "A[(Database)]",
    "A[[Subroutine]]",
    "A[/Parallelogram/]",
    "A[\\Trapezoid/]",
    "A[>Asymmetric]",
    'A["Already (quoted)"]',
    'A[Say "hi" (loud)]',
    "flowchart LR\n  A -->|Yes| B\n  A -->|No| C",
  ]) {
    test(`leaves valid source unchanged: ${valid.split("\n")[0]}`, () => {
      expect(sanitizeMermaid(valid)).toBe(valid);
    });
  }
});
