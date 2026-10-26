"use client";

import { useEffect, useId, useState } from "react";

import { removeMermaidArtifacts, sanitizeMermaid } from "@/lib/mermaid";

// Every element mermaid.render() touches is keyed off the id we hand it — the working div it appends
// to document.body, its own error-path cleanup, and our removeMermaidArtifacts(). A useId-derived id
// alone is stable across React StrictMode's double-invoked effect, so the discarded first pass's
// cleanup would delete the second pass's in-flight div and both renders would throw. Suffixing a
// process-wide sequence number makes each *invocation* own a distinct set of ids, so a superseded
// pass can only ever clean up what it created.
let renderSeq = 0;

// Renders a Mermaid diagram (the §6.3 visual-generator output). Client-only — mermaid needs the DOM.
// On a parse error it falls back to the raw code block, so a malformed diagram never breaks the lesson.
export function MermaidDiagram({ code }: { code: string }) {
  const id = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const renderId = `mmd-${id}-${++renderSeq}`;
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        // suppressErrorRendering is load-bearing: without it, a parse failure makes mermaid draw its
        // "Syntax error" bomb into the div it appended to document.body and then throw *before*
        // removing that div — orphaning the artifact outside the React tree, where it survives
        // unmount. With it, mermaid removes the temp div and rethrows, so we still fall back below.
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          securityLevel: "strict",
          suppressErrorRendering: true,
        });
        // Sanitise only what mermaid parses; the fallback below still shows the original source.
        const { svg } = await mermaid.render(renderId, sanitizeMermaid(code));
        if (!cancelled) setSvg(svg);
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        removeMermaidArtifacts(renderId);
      }
    })();
    return () => {
      cancelled = true;
      removeMermaidArtifacts(renderId);
    };
  }, [code, id]);

  if (failed) {
    return (
      <pre className="overflow-x-auto rounded-lg bg-ink-900 p-4 text-sm text-text-muted">
        <code>{code}</code>
      </pre>
    );
  }
  if (!svg) {
    return <p className="text-sm text-text-muted">Rendering diagram…</p>;
  }
  return (
    <div
      className="my-4 flex justify-center [&_svg]:max-w-full"
      // mermaid renders to SVG markup; securityLevel:'strict' sanitizes it before we inject.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
