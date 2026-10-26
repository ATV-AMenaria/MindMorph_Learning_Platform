// Helpers for rendering generated Mermaid diagrams safely. Pure string/DOM utilities so they can be
// unit-tested without a browser page.

// Mermaid's parser rejects unquoted parentheses/brackets inside a square-bracket node label —
// `A[Raw Files (CSV, JSON, SQL)]` is a syntax error. Generated diagrams hit this routinely, so quote
// those labels before handing the source to mermaid. Deliberately conservative: a label that already
// contains a quote, or that opens with a shape modifier (`[(`, `[[`, `[/`, `[\`, `[>`), is left
// exactly as written so valid diagrams round-trip byte-identical.
const NODE_LABEL_RE = /(\w)\[([^\]\n"]*)\]/g;
const SHAPE_MODIFIER_START = /^[([/\\>]/;
const NEEDS_QUOTING = /[()[\]]/;

export function sanitizeMermaid(code: string): string {
  return code.replace(NODE_LABEL_RE, (match, idTail: string, label: string) => {
    if (!NEEDS_QUOTING.test(label) || SHAPE_MODIFIER_START.test(label)) {
      return match;
    }
    return `${idTail}["${label}"]`;
  });
}

// mermaid.render() builds its diagram inside a div it appends to document.body and only removes that
// div on the success path. Belt-and-braces removal of every element it could have left behind, keyed
// on the render id we passed in (mermaid derives `d<id>` / `i<id>` from it).
export function removeMermaidArtifacts(renderId: string): void {
  if (typeof document === "undefined") return;
  for (const elementId of [renderId, `d${renderId}`, `i${renderId}`]) {
    document.getElementById(elementId)?.remove();
  }
}
