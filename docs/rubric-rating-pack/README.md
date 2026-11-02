# Independent rubric review

Start with [REVIEW.md](REVIEW.md), the readable pack of 60 candidate submissions. Record your ratings in [ratings.json](ratings.json). It contains 20 submissions in each of three categories: code review, analytical design and written practice.

For every submission:

1. Read the task, its four criteria, and the candidate answer.
2. Assign each criterion 0–25 points based on the answer. In `human_criteria`, record `criterion_id`, `earned_points` and a short quoted `evidence` list. Explain missing or contradicted evidence in `human_rationale`.
3. Sum the four points and fill `expected_band`: `mastered` for 80–100, `in_progress` for 40–79, or `remediation` for 0–39. These are review labels; they do not award learner mastery.
4. For the five `boundary` candidates in each category, confirm whether the answer is ambiguous or near a decision boundary. If not, flag it for replacement before the benchmark. Resolve uncertain ratings with another qualified human and record that review.

At the top of the JSON, fill `provenance.kind` with `human` or `expert`, `labeler` with the reviewer identity and relevant expertise, and `review_reference` with a dated review note or record. Set `not_model_generated` to `true` only when the labels were independently assigned by humans. Leave all these fields unset until review is complete. Do not copy ratings from an AI.

The candidate submissions themselves are synthetic and were prepared by the coding assistant. No expected grades or judge outputs are prefilled. Preserve their text and hashes. Each category has five task groups with four submissions each; this is a limited benchmark, not evidence for all possible subjects. Reserve it for evaluation. If the pack is used to tune the judge, obtain a fresh held-out pack instead.

Return the completed ratings for validation and bounded live judging. At least 20 held-out labels per category, 95% mastery-decision agreement, zero critical false mastery and stable repeated boundary decisions are required. A passing report is review evidence; enabling numeric rubric mastery still requires a separate server policy decision. Until then, feedback remains formative.
