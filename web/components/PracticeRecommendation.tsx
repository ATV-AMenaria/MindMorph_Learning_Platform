import type { KnowledgeTracking, PracticeMode } from "@/lib/types";

const labels: Record<PracticeMode, string> = {
  foundation: "Strengthen foundations",
  standard: "Keep practicing",
  challenge: "Try an application challenge",
};

export function PracticeRecommendation({ tracking }: { tracking?: KnowledgeTracking }) {
  if (!tracking) return null;
  const ready = tracking.status === "ready" && tracking.probability !== null;
  const percent = ready ? Math.round(tracking.probability! * 100) : null;
  return (
    <aside aria-label="Practice recommendation" className="mt-4 rounded-xl border border-white/10 bg-ink-900/60 p-4">
      <p className="text-sm font-medium text-text-strong">
        {tracking.status === "unavailable" ? "Recommendations temporarily unavailable"
          : ready ? labels[tracking.recommendation.mode] : "Building your learning estimate"}
      </p>
      <p className="mt-1 text-sm text-text-muted">{tracking.recommendation.reason}</p>
      <p className="mt-2 text-xs text-text-muted">
        Based on {tracking.observation_count} distinct {tracking.observation_count === 1 ? "exercise" : "exercises"} for this skill.
      </p>
      {percent !== null && (
        <p className="mt-1 text-xs text-text-muted">Experimental understanding estimate: {percent}%</p>
      )}
      <p className="mt-2 text-xs text-text-muted">Use New practice task to prepare your next exercise.</p>
    </aside>
  );
}
