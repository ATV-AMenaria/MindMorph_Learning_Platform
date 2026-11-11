# Experimental BKT practice recommendations

MindMorph can use exercise history to recommend the next practice task for a
skill. Bayesian Knowledge Tracing (BKT) estimates understanding; the existing
score thresholds, prerequisite locks and graph adaptation still decide progress.

## Enable locally

Install the updated backend requirements (`pip install -r requirements.txt`), then
set `MINDMORPH_BKT=1` in the backend environment and restart the API. The default is
off. No new service, API key, database column or migration is required.

PostgreSQL mode persists observations across restarts. Memory mode is intentionally
process-local. Turning the flag off hides recommendations and restores standard
practice generation; it does not delete evidence already collected.

## Learner flow

1. Open an unlocked skill and read its lesson. Reading and **Run** do not update BKT.
2. Submit an exercise for grading. Its first progress-eligible result becomes one
   observation: score >=80 is successful; a lower score is unsuccessful.
3. Complete new exercises for the same skill. Resubmitting the same exercise still
   affects normal grading, but does not add observations. Evaluator revisions also
   do not make the same exercise new evidence.
4. After three distinct exercises, the lesson shows an experimental understanding
   estimate and recommendation. Before that, it asks for more new exercises.
5. Click **New practice task** to generate using the recommendation. The current
   task is never automatically replaced. Existing prerequisite locks take priority.

The modes are foundation (estimate below 60%), standard (60% to below 90%) and
application challenge (90% or higher). Foundation tasks include a worked example
with different inputs. Challenges stay within the current skill and runtime.

## Model and evidence contracts

The pinned library is `pyBKT==1.4.3`. The adapter uses single-threaded, one-step
prediction with an unobserved trailing opportunity to read the state **after**
the last outcome and learning transition. Incorrect/correct use the library's
low-level 1/2 encoding; 0 means unobserved. It never fits during a request.

The fixed `bkt-v1` pilot settings are prior=0.20, learn=0.10, guess=0.20,
slip=0.10 and forget=0. These settings are uncalibrated. The model's probability
is distinct from an exercise score and from its predicted success on another task.

One exercise submission is one opportunity; hidden tests are correlated checks of
the same submitted work. The first eligible result is retained even if later
submissions improve. Practice-generation guidance is frozen when the durable
creation request is accepted, so a recovered request does not change its mode.

Run executions, diagnostic quizzes, standalone practice, incomplete assessments,
platform failures and uncalibrated rubric feedback are excluded. This means many
non-coding case studies currently supply no eligible evidence. Multi-skill tasks
are attributed only to their saved roadmap node: BKT does not diagnose specific
misconceptions, account for item difficulty, or model time-based forgetting here.
Generated foundation/challenge tasks can differ in difficulty, so these pilot
estimates should guide optional practice, not serve as calibrated mastery claims.

## Persistence and recovery

The existing `grading_records` table receives `kind=knowledge_observation` rows.
Its existing JSON `data` holds session/node/exercise IDs, source attempt, exercise
revision, boolean outcome, application sequence, model version and creation time.
Actual grades remain on the existing attempt records.

`learning_sessions.data.node_state[node].knowledge_tracking` contains only the
latest public summary: status, probability, observation count, model version and
recommendation. The full history stays outside the session blob.

The ledger row, summary, session revision and attempt's applied marker commit in
the same transaction. Observation order is committed application order, including
reconciled attempts. Concurrent stale writers receive the existing revision
conflict; reconciliation applies saved results without regrading. History reads
filter by user, session and node and do not use the capped general-record list.

If prediction is unavailable, a valid grade and its observation are still saved.
The public summary has no probability and recommends standard practice. The next
eligible grading or new-practice request replays durable observations to recover.
Existing sessions start collecting after activation; best scores and old feedback
are never expanded into fabricated history.

## Verification and demonstration

```powershell
python -m pytest tests/test_knowledge_tracking.py tests/test_bkt_practice.py tests/test_bkt_evaluation.py -q
python scripts/verify_grading_storage.py
python -m evals.bkt_evaluation --demo
```

The storage script provisions and removes an owned disposable PostgreSQL container,
runs migrations on that container only, and checks grading and BKT restart,
conflict and concurrent-write behavior. It needs the existing local worker Docker
CLI configuration. CI includes these checks in its disposable PostgreSQL job too.

The demo is explicitly simulated: Rahul's distinct scores 50,50,100 give about
43% and foundation guidance; Priya's 100,100,100 give about 97% and challenge
guidance. Both remain mastered under the existing progression rule. These are
parameter-dependent examples, not measured learner outcomes. Browser tests cover
both recommendations, explicit task replacement, reloads and saved draft recovery.

For local operator evaluation of an actual saved skill:

```powershell
python -m evals.bkt_evaluation --user-id USER --session-id SESSION --node-id NODE
```

The command is read-only and reports aggregate chronological next-exercise Brier
score and log loss against last-outcome and smoothed historical-rate baselines.
It excludes the first opportunity of each history and forecasts before reading
the evaluated answer. It does not export submissions or infer a training dataset.
Small samples and changing generated-task difficulty limit interpretation;
improvement in real learning requires a separate learner evaluation.
