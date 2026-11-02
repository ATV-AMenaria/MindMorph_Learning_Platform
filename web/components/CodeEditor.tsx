"use client";

import Editor from "@monaco-editor/react";
import { Component, type ErrorInfo, type ReactNode, useEffect, useState } from "react";

// Unknown languages retain their identity and use plain text highlighting.
const MONACO_LANGUAGE: Record<string, string> = {
  python: "python",
  javascript: "javascript",
  sql: "sql",
  typescript: "typescript",
  ts: "typescript",
  c: "c", cpp: "cpp", java: "java", csharp: "csharp", go: "go", rust: "rust", r: "r",
  postgresql: "sql", react: "typescript", html: "html", css: "css", json: "json",
};

// Monaco editor for coding_challenge solutions (replaces Streamlit's st_ace). Returns the raw code
// string; server-side hardened-sandbox grading is unchanged.
export function CodeEditor({
  value,
  onChange,
  language = "python",
}: {
  value: string;
  onChange: (v: string) => void;
  language?: "python" | "javascript" | "sql" | string;
}) {
  const [plainMode, setPlainMode] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (ready || plainMode) return;
    const timeout = window.setTimeout(() => setPlainMode(true), 6000);
    return () => window.clearTimeout(timeout);
  }, [ready, plainMode]);
  const monacoLang = MONACO_LANGUAGE[language.toLowerCase()] ?? "plaintext";
  return (
    <div className="overflow-hidden rounded-xl border border-white/10">
      {plainMode ? <><p className="bg-ink-850 px-4 py-2 text-xs text-text-muted">Using accessible plain text because the code editor did not finish loading.</p><PlainEditor value={value} onChange={onChange} /></> : <>
        {!ready && <div className="flex h-[300px] items-center justify-center bg-ink-900 text-sm text-text-muted"><span>Loading code editor…</span><button type="button" onClick={() => setPlainMode(true)} className="ml-3 underline">Use plain text now</button></div>}
        <EditorBoundary fallback={<PlainEditor value={value} onChange={onChange} />}>
          <Editor
            height="300px"
            language={monacoLang}
            theme="vs-dark"
            value={value}
            onMount={() => setReady(true)}
            onChange={(v) => onChange(v ?? "")}
            options={{ fontSize: 14, minimap: { enabled: false }, scrollBeyondLastLine: false, tabSize: language.toLowerCase() === "python" ? 4 : 2, padding: { top: 12, bottom: 12 }, ariaLabel: "Your solution" }}
          />
        </EditorBoundary>
      </>}
    </div>
  );
}

function PlainEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return <textarea aria-label="Your solution" value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} className="h-[300px] w-full resize-y bg-ink-900 p-4 font-mono text-sm text-text-strong" />;
}

class EditorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* plain-text editing remains available */ }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
