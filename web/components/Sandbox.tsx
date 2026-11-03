"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { RunResult } from "@/lib/types";
import { newRequestId, useDraft } from "@/lib/useDraft";

const RETRYABLE = new Set(["pending"]);

// Run is an explicit, non-grading action against the matching server profile. Its state is separate
// from Submit so experimentation never changes mastery or practice history.
export function Sandbox({ language, solution, basePath, revision, profileId }: { language: string; solution: string; basePath: string; revision?: string; profileId?: string }) {
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const reusableRun = useRef<{ id: string; identity: string } | null>(null);
  const input = useDraft(`run-input:${basePath}`, revision ?? "legacy");
  const identity = `${basePath}\n${revision ?? "legacy"}\n${solution}\n${input.value}`;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  useEffect(() => { setResult(null); setError(null); }, [identity]);

  useEffect(() => () => {
    const active = controller.current;
    const run = reusableRun.current;
    active?.abort();
    if (active && run) void api.cancelRun(basePath, run.id).catch(() => undefined);
  }, [basePath]);

  async function run() {
    const runId = reusableRun.current?.identity === identity ? reusableRun.current.id : newRequestId();
    reusableRun.current = { id: runId, identity };
    const aborter = new AbortController();
    controller.current = aborter;
    setRunning(true);
    setError(null);
    const timeout = window.setTimeout(() => {
      aborter.abort();
      void api.cancelRun(basePath, runId).then((r) => { if (identityRef.current === identity) { setResult(r); reusableRun.current = null; } }).catch(() => undefined);
    }, 125_000);
    try {
      const response = await api.run(basePath, solution, revision, runId, aborter.signal, input.value);
      if (identityRef.current !== identity) return;
      setResult(response);
      if (!RETRYABLE.has(response.status)) reusableRun.current = null;
    } catch (e) {
      if (identityRef.current === identity) setError(e instanceof Error ? e.message : "Run failed. Your solution is still available above.");
    } finally {
      window.clearTimeout(timeout);
      if (controller.current === aborter) {
        controller.current = null;
        setRunning(false);
      }
    }
  }

  async function cancel() {
    const active = controller.current;
    if (!active) return;
    active.abort();
    const currentRun = reusableRun.current;
    if (currentRun) {
      try {
        const cancelled = await api.cancelRun(basePath, currentRun.id);
        if (identityRef.current === currentRun.identity) setResult(cancelled);
        reusableRun.current = null;
      } catch {
        setError("Cancellation is still being confirmed. You can retry this run safely.");
      }
    }
  }

  return (
    <section className="mt-6 rounded-xl border border-white/10 bg-ink-900/70 p-4" aria-label="Run your solution">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h3 className="font-medium text-text-strong">Try it live</h3><p className="mt-1 text-xs text-text-muted">Use the {language} runtime when available. Running does not submit or change your progress.</p></div>
        {running ? <button type="button" onClick={cancel} className="accent-ring rounded-lg border px-4 py-2 text-sm">Cancel run</button> : <button type="button" onClick={run} disabled={!solution.trim()} className="accent-ring rounded-lg border px-4 py-2 text-sm disabled:opacity-50">Run solution</button>}
      </div>
      <label className="mt-3 block text-xs text-text-muted">{profileId === "react-dom" ? "Browser actions and queries (JSON)" : "Program input (stdin)"}
        <textarea aria-label={profileId === "react-dom" ? "Browser actions and queries" : "Program input"} value={input.value} onChange={e => input.setValue(e.target.value)} spellCheck={false} placeholder={profileId === "react-dom" ? '{"actions":[],"queries":[{"selector":"#root","read":"text"}]}' : "Paste sample input here, if the program needs it."} className="accent-ring mt-1 h-20 w-full rounded-lg border border-white/10 bg-ink-900 p-2 font-mono text-sm text-text-strong" />
      </label>
      {running && <p role="status" className="mt-3 text-sm text-text-muted">Running…</p>}
      {error && <div role="alert" className="mt-3 text-sm text-amber-300">{error} <button type="button" className="ml-2 underline" onClick={run} disabled={running}>Retry</button></div>}
      {result && <div className="mt-3" aria-live="polite"><p className="text-sm text-text-muted">Run status: {result.status}</p>{result.message && <p className="mt-1 text-sm text-text-muted">{result.message}</p>}{result.stdout && <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/30 p-3 text-xs text-text">{result.stdout}</pre>}{result.stderr && <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/30 p-3 text-xs text-amber-200">{result.stderr}</pre>}</div>}
    </section>
  );
}
