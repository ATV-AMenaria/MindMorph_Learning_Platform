import type { Exercise } from "@/lib/types";

export function AssessmentNotice({ exercise }: { exercise: Exercise }) {
  const status = exercise.status ?? exercise.assessment?.status;
  if (!status && !exercise.instructions) return null;
  return <aside className="mt-4 rounded-xl border border-white/10 p-4 text-sm text-text-muted">
    <p className="font-medium text-text-strong">{status === "ready" ? "Assessment available" : status === "feedback_only" ? "Formative review — progress is unchanged" : "Assessment needs verification — progress is unchanged"}</p>
    {exercise.availability?.reason && <p className="mt-1">{exercise.availability.reason}</p>}
    {exercise.execution_profile && <p className="mt-1">Runtime: {exercise.execution_profile}</p>}
    {exercise.instructions && <p className="mt-2 whitespace-pre-wrap">{exercise.instructions}</p>}
    {exercise.rubric?.length ? <ul className="mt-2 list-inside list-disc">{exercise.rubric.map((c, i) => <li key={c.criterion_id ?? i}>{c.criterion} ({c.max_points} points maximum)</li>)}</ul> : null}
  </aside>;
}
