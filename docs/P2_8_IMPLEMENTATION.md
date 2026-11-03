# P2 #8 implementation and local activation — 31 October 2026

The goal is a dependable goal → path → diagnostic → lesson → practice → assessment → saved progress journey. **The remaining implementation is complete and locally activated.** React/browser execution, bounded semantic evaluator repair, creation crash recovery and durable activation are implemented and verified. **Full live-AI acceptance remains open under the retained evaluation cap.** Independently labelled rubric calibration has not been performed; all rubric review remains formative.

## Implemented behavior

- Diagnostics validate real skill IDs and unique questions before publication. Skip and resume are explicit saved actions. Skipping does not record a zero, and correct diagnostic answers retain the score-60 head start rather than granting mastery.
- Learning creation retries retain the original goal, preferences, domain, language and request identity. Completed requests replay their saved response. Recoverable generation leases fence late producers; the generated result is persisted before materialization. A retry after a crash reuses its identities and preserves learner work saved after the first write. SSE persistence/preflight runs off the event loop.
- New exercise generation negotiates installed runtime capabilities first. Unknown languages retain their identity. Explicit coding goals in broader subjects, including Finance + Python, can produce coding practice.
- Private evaluator packages, references and answer keys are separated from learner responses. Diagnostic keys appear only in submitted review. Generated evaluator claims cannot certify their own release.
- Executable evaluators must pass reference, alternate-correct, two negative and mandatory empty-program controls. Trusted parent code compares bounded observations; learner stdout cannot report its own score. A failed release check gets one persisted semantic repair for the saved task/evaluator/profile fingerprint. Reopening cannot reset that budget. Host outages and interrupted preflight recover through leased availability checks without consuming a semantic repair. A still-failing task remains ungraded.
- Typed evidence is validated before mastery, events or adaptation. Missing, nonfinite, incomplete and formative results never become zero-score practice. Rubric review uses the saved task, published criterion bounds and literal submission evidence; **all rubric categories remain formative without numeric mastery awards** pending independent calibration.
- Stable attempt IDs bind the owner, context, exercise revision, evaluator and submission. Terminal outcomes survive retries and concurrent session updates. Reconciliation applies a saved outcome once without rejudging. The web UI offers recovery after a conflict. An explicit retry after a terminal platform failure creates a fresh attempt; a lost transport response retains the same identity.
- Standalone exercises have saved identities, assessment, run controls, history and reload recovery. They do not invent roadmap mastery.
- Drafts are scoped to learner, context and exercise revision, with explicit restore from an earlier revision and a memory fallback when storage fails. Editor loading has a finite plain-text fallback. Run accepts persisted stdin or browser-action JSON, binds the input to its run ID, supports cancellation, and never awards progress.
- **New practice task** explicitly generates a versioned replacement for saved teaching. It preserves lesson content and mastery, retains the old draft for explicit restore, stages generation durably, and replaces the old exercise only after assessment validation. Transport retries reuse the staged task. Existing legacy tasks can retain their original content while the learner requests fresh assessable practice.
- Live findings led to concise capability-specific evaluator schemas, bounded setup output, removal of duplicated repair input, and clearer task-generation constraints: plain-text written submissions, supported input/output contracts, consistent examples and beginner scope. These refinements passed offline checks; their full live-generation matrix remains unverified under the cap.

## Worker operation

Every project command runs in the `mindmorph` conda environment. Request handling never builds images, pulls dependencies, installs packages or runs learner code on the API host. The server-owned `MM_WORKER_CLI` JSON prefix overrides the ignored local `worker/host.local.json`; the default is `docker`. This machine uses its existing WSL Ubuntu Docker daemon.

`tools/runtime_profiles.py` declares capabilities. `worker/profiles.lock.json` enables only a matching manifest revision, immutable local image digest, exact installed version inventory and passing probes. A missing image or stale lock reports unavailable. Image locks are local deployment evidence; another host must explicitly build and probe its own images.

```powershell
conda run -n mindmorph python -m worker.build_profiles
conda run -n mindmorph python -m worker.probe_profiles
conda run -n mindmorph python -m worker.build_profiles --tools
conda run -n mindmorph python -m worker.probe_tools
conda run -n mindmorph python -m worker.build_profiles --browser
conda run -n mindmorph python -m worker.probe_browser
conda run -n mindmorph python scripts/verify_grading_storage.py
conda run -n mindmorph python scripts/run_local_api.py
```

All **14 profiles** are available on this host: Python core, Python CPU data/science, Node JavaScript, compiled TypeScript, SQLite, PostgreSQL, C, C++, Java, C# through Mono, Go, Rust, base R and real React/TSX browser execution. TypeScript includes pinned Node types and an actual stdin compile/run check. C# uses Mono; R supports its pinned base installation.

The browser template typechecks and bundles App.tsx, then mounts it in Chromium through a trusted action/query observer. Submitted code runs in the browser. Expected outputs remain outside the worker. The profile pins React 19, TypeScript 5.7.3, Playwright-core 1.60.0 and its image digest; the observed Chromium version is recorded in `worker/dependencies.lock.json`. Its seccomp profile derives from [Playwright's Docker guidance](https://playwright.dev/docs/docker), adding the sandbox chroot syscall needed with dropped container capabilities. Chromium's sandbox remains enabled.

Workers run as non-root with no external network, host mounts, capabilities or application secrets, a read-only image, bounded writable tmpfs, finite process/output/time limits and whole-container cleanup. Native binaries require executable `/work` tmpfs. API-owned Run, grading and preflight-control jobs share PostgreSQL admission across processes: at most two jobs, one per learner, eight queued and 2 GiB combined worker memory. Admission queries lock the target and currently live jobs rather than terminal history. Grading has a 90-second evaluation budget; publication controls have a 170-second budget. A host outage is ungraded. Ordinary missing-evaluator recovery uses the negotiated contract or formative review; legacy host execution requires the explicit trusted-local opt-in and is never used for newly generated packages.

The existing database was backed up, its dump validated, and migration **0006_grading_records** applied without changing prior rows. The live memory app was freshly exported and all **four paths and six document chunks** imported and verified. Lesson text, tasks, mastery, diagnostics, chat, document vectors and source metadata were preserved; private graders moved into scoped records. A separate API passed readiness and real HTTP checks before port 8000 was switched. The live API now reports `store=postgres, durable=true`; the frontend remains on port 3000. No commit or remote deployment was performed.

`scripts/run_local_api.py` keeps the configured WSL host awake and supports owned graceful shutdown. `scripts/migrate_p2_8.py`, `scripts/activate_p2_8.py` and `scripts/smoke_p2_8.py` retain the operator procedure. Activation refuses unrecognized listeners and unreviewed overwrites. Backups and activation evidence are private, Git-ignored files in `logs/p2-8-activation-20261030/`.

## Verified evidence

- Full backend regression: **532 passed, 19 skipped**, plus the subsequently added shared-model isolation check passed separately. Five PostgreSQL lifecycle checks passed separately against an owned disposable service; other service-gated checks retain their skip conditions.
- Real database checks covered migration preservation, fresh-process result replay, exactly-once reconciliation, expired leases, creation recovery and control-job memory admission across processes. Preserved pgvector nearest-neighbor retrieval, document metadata and owner scope were also checked without model inference.
- Final browser regression: **67/67 passed against the isolated production build**, including saved-input and new-task draft recovery. These use mocked API responses. `PLAYWRIGHT_PRODUCTION=1` selects `.next-p2-8-build`; the default remains a separate development test server. A read-only browser check against the activated port3000 app also rendered a preserved lesson and the new controls while blocking all backend writes and external requests.
- TypeScript checking and the isolated optimized production build passed. The user's original `web/tsconfig.json` bytes were restored after Next.js generated its build include. The AST knowledge graph was refreshed without LLM calls, and `git diff --check` passed.
- Real worker controls: **13 core/data/Node/TypeScript/SQLite**, **32 compiled/database**, and **13 real-browser** controls passed. The browser checks include state, accessible keyboard interaction, type/DOM failures, rejected promises, mocked APIs, network blocking, forged stdout, timeout and cancellation.
- Real HTTP smoke through PostgreSQL and isolated Python/React workers passed: correct work100, incorrect work0, stable replay, private-response filtering and owner scope. It used zero model calls, removed only its synthetic records, and preserved the four learner paths. Evidence: `logs/p2-8-activation-20261030/http-smoke.json` and `logs/p2-8-final-20261030/`.

## Live acceptance and calibration limits

**2 November authorization update:** the user subsequently approved **3,000,000 cumulative tokens**, retaining the **500-call and USD 5 ceilings**. All previous calls, uncertain reservations and observed usage remain in the original ledger. The prior 2M decision and figures below describe the earlier checkpoint, not a reset. A provider probe succeeded and live acceptance resumed. See [the follow-up record](P2_8_LIVE_FOLLOWUP.md) for current results.

The initial live run used the configured Groq/Ollama chain, paced requests and the same persisted ledger across repairs, fallbacks and restarts. It stopped before exceeding the original 2M-token bound. The user initially retained that cap; no additional inference was made until the later explicit authorization.

The ledger records **226 calls**, **503,362 provider-reported tokens**, and **1,994,827 tokens as the conservative upper bound** after reserving uncertain failed requests. Estimated on-demand cost is **USD 0.15876 observed / USD 0.54661 upper bound**, using the [documented Groq model rates](https://console.groq.com/docs/model/openai/gpt-oss-120b). Account billing was not measured. The 500-call and USD 5 ceilings were also retained.

**The full eight-journey and targeted live-generation matrix did not pass.** Findings included provider TPM throttling/413 errors, an unsupported attachment deliverable, and inconsistent evaluator controls. A resumable-harness hydration issue was also corrected; failed and superseded samples remain recorded. The later prompt/schema refinements were verified offline and are not presented as a completed live rerun. Logs and resumable states are in `logs/p2-8-live-20261030/`.

No independent human/expert-labelled rubric dataset is present. The user requested a human rating pack on 2 November; [the pack and instructions](rubric-rating-pack/README.md) now contain 60 unlabelled synthetic submissions for human review. `evals/rubric_calibration.py` enforces the proposed provenance/held-out/boundary/critical-control gates, and its policy tests passed using fake judges. Those tests are not calibration evidence. **All three review categories remain formative, with no numeric mastery eligibility.** Enabling rubric mastery still requires 20 held-out labels/category, 95% mastery-decision agreement, zero critical false mastery and repeated stable boundary decisions. The current product does not self-certify a model-generated benchmark.

The implementation/local activation is complete. The expanded roadmap item remains acceptance-open for the uncompleted live matrix; no broader AI-quality or calibrated-grade claim is made.

This work also advances P1 #5's grading/evaluation service. It does not close unrelated P1 #7 routing or ingestion work.
