"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { CodeEditor } from "@/components/CodeEditor";
import { AssessmentNotice } from "@/components/AssessmentNotice";
import { EvidenceSources, citedText } from "@/components/EvidenceSources";
import { GradeResult } from "@/components/GradeResult";
import { Sandbox } from "@/components/Sandbox";
import { api } from "@/lib/api";
import { useDraft, completeDraftAttempt } from "@/lib/useDraft";
import { useUser } from "@/lib/useUser";

export default function StandaloneExercisePage() {
  const { userId, ready } = useUser();
  const router = useRouter();
  const exerciseId = String(useParams().exerciseId);
  const queryClient = useQueryClient();
  const queryKey = ["exercise", userId, exerciseId];
  const exerciseQuery = useQuery({ queryKey, queryFn: () => api.getExercise(userId!, exerciseId), enabled: !!userId });
  const exercise = exerciseQuery.data;
  const revision = exercise?.revision ?? "legacy";
  const draft = useDraft(`${userId}:exercise:${exerciseId}`, revision);
  const grade = useMutation({
    mutationFn: () => api.gradeExercise(userId!, exerciseId, draft.value, exercise?.revision, draft.attemptId()),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, updated);
      const status = updated.grade_result?.status;
      if (status !== "pending") completeDraftAttempt(`${userId}:exercise:${exerciseId}`, revision);
    },
  });

  useEffect(() => { if (ready && !userId) router.push("/"); }, [ready, userId, router]);
  if (!userId || exerciseQuery.isLoading) return <main className="mx-auto max-w-4xl px-6 py-12 text-text-muted">Loading exercise…</main>;
  if (exerciseQuery.isError || !exercise) return <main className="mx-auto max-w-4xl px-6 py-12"><p role="alert">Could not load this exercise.</p><button className="mt-3 underline" onClick={() => void exerciseQuery.refetch()}>Retry</button></main>;

  const language = exercise.language ?? "plaintext";
  const isCoding = (exercise.format ?? "coding_challenge") === "coding_challenge";
  const result = grade.data?.grade_result ?? exercise.last_result;
  const runBasePath = `/exercises/${encodeURIComponent(userId)}/${encodeURIComponent(exerciseId)}/run`;

  return <main className="mx-auto max-w-4xl px-6 py-10 md:px-10">
    <button type="button" className="text-sm text-text-muted hover:text-text" onClick={() => router.push("/")}>← Saved exercises</button>
    <article className="surface mt-5 p-7 md:p-10">
      <p className="eyebrow mb-4">Standalone exercise</p>
      <div className="prose prose-invert lesson-prose max-w-none whitespace-pre-wrap">{citedText(exercise.statement ?? "Exercise statement unavailable.", exercise.grounding)}</div>
      <EvidenceSources evidence={exercise.grounding} />
      <AssessmentNotice exercise={exercise} />
      <div className="mt-6">{isCoding ? <CodeEditor value={draft.value} onChange={draft.setValue} language={language} /> : <textarea aria-label="Your response" value={draft.value} onChange={(e) => draft.setValue(e.target.value)} className="accent-ring h-48 w-full rounded-xl border border-white/10 bg-ink-900 p-4 text-text-strong" />}</div>
      {draft.storageUnavailable && <p role="status" className="mt-2 text-xs text-text-muted">Browser storage is unavailable. Keep this tab open to retain your draft.</p>}
      {draft.previous && <div className="mt-3 flex gap-3 text-sm"><span>A previous exercise draft is saved.</span><button type="button" className="underline" onClick={draft.restorePrevious}>Restore it</button><button type="button" className="underline" onClick={draft.dismissPrevious}>Dismiss</button></div>}
      <button type="button" onClick={() => grade.mutate()} disabled={grade.isPending || !draft.value.trim()} className="accent-ring mt-5 rounded-xl px-6 py-2.5 font-medium text-ink-900 disabled:opacity-50" style={{ background: "var(--color-gold)" }}>{grade.isPending ? "Assessing…" : "Submit response"}</button>
      {grade.isError && <div role="alert" className="mt-3 text-sm text-amber-300">{grade.error.message} Your response is saved above. <button type="button" className="underline" onClick={() => grade.mutate()}>Retry</button></div>}
      {result && <GradeResult result={result} />}
      {isCoding && <Sandbox language={language} solution={draft.value} basePath={runBasePath} revision={exercise.revision} profileId={exercise.execution_profile} />}
    </article>
    {exercise.history?.length ? <section className="surface mt-6 p-6"><h2 className="font-semibold">Previous attempts</h2><ol className="mt-3 space-y-3">{exercise.history.map((attempt, i) => <li key={attempt.attempt_id ?? i} className="border-t border-white/10 pt-3"><p className="text-sm text-text-muted">Attempt {i + 1}</p><GradeResult result={attempt} /></li>)}</ol></section> : null}
  </main>;
}
