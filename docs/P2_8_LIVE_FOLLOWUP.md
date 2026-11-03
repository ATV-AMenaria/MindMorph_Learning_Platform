# P2 #8 live acceptance follow-up — 2 November 2026

The user authorized a cumulative **3,000,000-token** ceiling, retaining **500 model calls and USD 5**. The original ledger and all reservations were preserved. Its amendment records the authorization; a byte-for-byte copy of the previous ledger is retained in the private evaluation logs. Provider quota renewal does not reset project usage.

The renewed Groq probe succeeded. The configured provider chain and isolated execution profiles were rechecked. **Five of eight learner journeys and four of eight targeted checks pass; full acceptance is still open.** Earlier failures and superseded tasks remain recorded. Failed publication checks continue to withhold grades rather than awarding progress.

| Suite | Passing | Still unsuccessful |
|---|---|---|
| Learner journeys | Python preference; JavaScript preference, focused and foundation; beginner guitar | Both SQL journeys; experienced guitar rubric review |
| Targeted generation | Python data/science; React/TypeScript in real Chromium; formative Haskell review with identity retained; Finance + Python | C++ and PostgreSQL generation; written analytical review; Music + Python evaluator |

The ledger ends at **408 calls, 1,005,453 provider-reported tokens and 2,950,502 tokens conservatively reserved/observed**, leaving 49,498 upper-bound tokens. All 226 original calls remain byte-equivalent as data, with no reservation reset. This is not a claim that 2.95M tokens were billed. No additional live calls are scheduled by this checkpoint.

Provider failures include TPM throttling, structured-response HTTP400 errors and an unavailable local fallback. Written review still sometimes supplies nonliteral evidence and is rejected. The Music + Python task incorrectly expected 293.66Hz for a -5-semitone offset where its own reference produced 329.63Hz; the task was withheld. These are failed live acceptance cases, not passed safety tests or completed calibration.

The acceptance harness was repaired to select nonempty negative-control code rather than expecting a numeric grade for an empty submission. It now requires complete formative criterion feedback and refuses to trust an old `complete` flag hiding a platform-error review. A fresh explicit review attempt reuses the saved synthetic answer. Current task-generation checks use a new versioned replacement request; saved teaching and work are preserved. Failed targeted attempts now retain private evaluator/control snapshots, and prior results are archived before retry.

Live testing found a stale format-selector prompt promising only Python/JavaScript/SQLite and denying browser support. It now chooses modality independently of installed runtimes and preserves requested language/dialect, including unsupported code reviewed formatively. The browser execution contract now includes exact flat JSON examples, distinguishes action `type` from query `read`, and explains ARIA attribute versus snapshot reads. The published example passed a zero-model real Chromium run, followed by a passing live-generated React case. Rubric feedback failures now carry safe stage codes without private exception text; exact quotation guidance was clarified without relaxing evidence checks.

**47 final affected grading/routing/evaluation checks passed**, plus **three operator ownership-safety checks** separately. These are safety/regression tests, not proof of live calibration. The user’s original `web/tsconfig.json` is preserved.

Direct HTTP checks confirm the API on8000 is PostgreSQL-backed and durable, and the frontend on3000 returns200. The original existing database container was restarted without creating storage or applying migrations. Listener ownership discovery now fails closed on Windows query errors rather than treating a failed query as a vacant port; unrecognized listeners were not stopped. `scripts/run_local_api.py --start-existing-db` optionally starts only the existing `mindmorph-db` container after verifying its `pgvector/pgvector:pg16` image. Run local servers in a persistent terminal/session.

## Independent human review

The [rating pack](rubric-rating-pack/README.md) is prepared: **60 unlabelled submissions**, 20 per category, across five task groups per category. It includes English/Hindi responses, short/long responses, five boundary candidates and five critical injection controls per category. Candidate submissions are assistant-authored; expected grades, reviewer provenance and human criterion ratings are deliberately blank. Related task variants are identified and are not claimed to be independent domains. The reviewer must confirm or replace the boundary candidates.

No independent human ratings have yet been supplied and no calibration benchmark has passed. All rubric review remains formative. Preparing the pack does not enable numeric rubric mastery. After human review, the pack needs provenance validation and live judge comparison within the remaining authorized budget, followed by a separate runtime policy decision.

Private operator evidence remains in `logs/p2-8-live-20261030/` and `logs/p2-8-recheck-20261102/`. Live acceptance remains open until the required checks actually pass.
