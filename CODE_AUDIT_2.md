# MindMorph — Code Audit 2

## Implementation update — 16 October 2026

**The approved grading and failed-test repair is implemented locally.** The original audit below is
retained as a historical record; its failing-test totals describe the checkout **before** this repair.

| Verification | Before repair | After repair |
|---|---|---|
| Windows backend | 288 passed, 13 failed, 3 skipped | **385 passed, zero failed, 3 DB checks skipped** |
| Ubuntu backend, Python 3.11 / Node 20 | Not run | **385 passed, zero failed, 3 DB checks skipped** |
| Final grading-runner regressions, including paths with spaces | No coverage for these cases | **93 passed on Windows and 93 passed on Ubuntu** |
| Browser suite | 37 passed, 4 failed | **44 passed, zero failed** |
| Final grading-error UI check | No dedicated check | Passed; learner sees no zero and progress stays unchanged |
| Type check / clean production build | Passed | **Passed** |

The final Ubuntu runner check used a fresh Linux-local source copy and Node executable. One
follow-up run directly from the Windows-mounted folder hit a JavaScript timeout; the isolated rerun
passed all 93 checks in 21.87 seconds with the same ten-second execution limit. The Windows runner
check passed all 93 in 34.61 seconds. The production timeout was not increased to obtain a pass.

### What changed and why

| Area | Repaired behavior |
|---|---|
| JavaScript grading — F02/F03 | Tests run once through Node's native test lifecycle. Promises/callbacks complete before scoring; suites/parent wrappers and skipped tests do not inflate leaf counts. Empty or wholly skipped artifacts are ungraded. Legacy exported tests remain supported. |
| Python grading — additional verified defects | Async tests are awaited. Empty modules, required-fixture signatures and generator tests are rejected as grading-artifact defects; plain module assertions and optional arguments remain compatible. |
| SQL grading — F01 | Saved assertion strings are decoded and serialized as one array. Verification SELECTs are read-only, including through comments/CTEs; malformed statements/expected values are rejected. Learner schema/data mistakes still produce real failed grades. |
| Grade integrity | Scores come from separate JSON result files, not learner stdout. Exit/completion/count checks reject crashes or contradictory “all passed but failed” reports. Diagnostics are bounded and Unicode survives. |
| Language agreement — part of F14 | Execution aliases normalize consistently. Cached exercise/artifact disagreement and explicit unsupported execution languages produce “not graded”, not Python fallback. Older Python artifacts and known case-study metadata remain compatible. |
| Learner progress | HTTP tests run all three real graders and verify persistence in the memory repository. Broken grading artifacts preserve attempts/mastery and do not trigger adaptation; genuinely wrong answers still remediate. |
| Windows paths/order — F19/F20 | Grading paths are redacted on Windows and Unix, including directory names with spaces. Equal save timestamps use a monotonic sequence tie-break, including re-saving an existing session. |
| Failed regressions — F18, agreed subset | Updated backend mocks/fakes retain explicit contract checks. Browser tests follow the actual question → summary → roadmap flow and preserve keyboard/lock/animation checks. Added JS/SQL editor language and error-feedback checks. |
| Test isolation and CI | Playwright owns port 3102 and `.next-e2e`; `PLAYWRIGHT_PORT` can override the port. Windows/Linux backend CI installs Node and bounds database connection checks. Production build verified in a clean temporary copy. |

```mermaid
flowchart LR
    A["Learner submits code"] --> B["Validate language and tests"]
    B -->|Valid| C["Python / Node / SQLite<br/>Run real checks"]
    B -->|Broken artifact| G["Not graded<br/>Progress unchanged"]
    C --> D["Read separate result file<br/>Check completion and counts"]
    D -->|Measured result| E["Score actual tests<br/>Apply existing mastery rules"]
    D -->|Invalid or crashed| G
```

**Scope remaining:** F04 database fallback, broader F05/F06 personalization/domain behavior,
F07/F08 assessment state, F09 onboarding recovery, F10 graph integrity, F11 stale/locked submissions,
F12 concurrency, F13 readiness, content/example propagation in F14, F15 direct responses,
F16/F17 wording/accessibility, F21 dependency locking, and public-service prerequisites remain
separate work. Green tests do not mean those deferred findings were fixed.

**Verification limits:** the three real PostgreSQL/pgvector checks remain skipped; live AI output
quality and a public deployment were not verified. `graphify update .` could not run because graphify
is not installed and its report directory is absent. Existing local application changes were
preserved. No Git commit, push, merge or deployment was performed.

---

> **Plain-English handover, implementation check, and fix/testing plan**  
> **Audit date:** 16 October 2026, India time.  
> **Snapshot:** `feature/multi-language-grader`, commit `25810af`, plus the local changes present during this audit.  
> **Primary reference:** [Implementation status](docs/IMPLEMENTATION_STATUS.md). Findings are checked against source code and Git history, rather than assuming the older documents are correct.

## 1. The current position at a glance

| Question | Answer |
|---|---|
| What was already built when you first downloaded the project? | The roadmap → lesson → exercise → grade → adaptation loop, web app, database integration, PDF retrieval, tutor, and diagnostic quiz. Most of this came from the original project. |
| What did you add and push? | Commit `25810af`: JavaScript and SQL grading alongside Python, language-aware editor changes, richer grading feedback, and lesson loading skeletons. |
| Where is that pushed? | Your fork, `ATV-AMenaria/MindMorph_Learning_Platform`, branch `feature/multi-language-grader`. GitHub branch hashes were checked directly during this audit. |
| Is it on `main`? | No. GitHub `main` still points to `0cc8c13`. A pushed feature branch does not mean the change was merged or deployed. |
| What came after the push? | Follow-up questions, stored learning preferences, scope/language controls, broader subject prompts, path summary, assessment answer review, “Start Here”, and database fallback/startup changes. These are local and uncommitted. |
| How large is the local work? | **30 modified tracked files**, with **692 added / 215 removed lines**, plus **9 untracked files** before this report was created. Untracked file contents are not included in those line counts. |
| Is the current implementation flawless? | No. Important gaps remain in grading accuracy, saving progress, preference handling, assessment state, and verification. Several new screens are further along than the behavior behind them. |
| Is the live deployment verified? | No deployment URL or deployed commit was available. This report verifies the checkout and GitHub branch heads. The running public service, hosting configuration, and deployed database still need a separate check. |

**Reading key:** ✅ implemented · 🟡 partial or needs validation · 🔴 concrete defect · ⏳ future scope · ❓ not verified. “Implemented” means code exists; it does not automatically mean the feature passed a live end-to-end test.

```mermaid
flowchart LR
    A["Original project<br/>0cc8c13"] --> B["Your pushed work<br/>25810af<br/>Python + JS + SQL grading"]
    B --> C["Local work after push<br/>Personalization + assessment UI<br/>Database behavior changes"]
    C --> D["Current task<br/>Fix correctness gaps<br/>Prove the learning loop works"]
    D --> E["Then take up<br/>new product tasks"]
    style B fill:#d8f3dc,color:#111
    style C fill:#fff3bf,color:#111
    style D fill:#ffd8a8,color:#111
```

## 2. What happened from first download to now?

The dates below come from Git's local history/reflog. The first recorded action is a **clone**, rather than a later `git pull`.

| When, India time | Evidence | What happened | Meaning |
|---|---|---|---|
| 29 Sep 2026, 12:23 | Clone reflog, `0cc8c13` | Downloaded `tarunlnmiit/MindMorph_Learning_Platform` | This is the starting point for separating inherited work from your additions. |
| 6 Oct, 11:29 | Branch reflog | Created `feature/multi-language-grader` | Your development moved onto a separate branch. |
| 6 Oct, 11:31–11:34 | Commit + amend reflog | Created the grading change, then amended it to `25810af` | The earlier `fef7894` was replaced by the amended commit; it is not a second feature release. |
| 6 Oct, 11:55 | Remote reflog | Original remote renamed to `upstream` | `upstream` identifies the original author's repository. |
| 6 Oct, 11:56 | `update by push` reflog | Pushed `25810af` to your fork's feature branch | This is positive evidence of the push. |
| 6 Oct, 12:35 | Fetch reflog | Recorded your fork's `main` at `0cc8c13` | Main stayed at the inherited baseline. |
| After the pushed snapshot | Working-tree diff and new files | Added the local personalization, UI, and persistence changes | These changes are present on disk, but absent from the pushed commit. |
| 16 Oct, this audit | Read-only GitHub `ls-remote` | Feature branch = `25810af`; main = `0cc8c13` | Confirms the remote branch heads still match this picture. |

| Location | What it contains now | Backup/release status |
|---|---|---|
| Local `main` | Original baseline, `0cc8c13` | No personalization work here. |
| GitHub `origin/main` | Original baseline, `0cc8c13` | The pushed grading feature has not reached main. |
| GitHub `origin/feature/multi-language-grader` | Baseline + `25810af` | Grading implementation is backed up remotely. |
| Local current branch, committed part | Same `25810af` | No additional committed changes beyond the feature branch head. |
| Local working files | Additional modifications and new files | These need review, tests, and a commit before they are backed up through Git. |
| Live application | Unknown deployed revision | Match its deployed commit to one of the rows above before assuming it contains the latest work. |

**What was worked on last?** The latest visible development theme is the broader learning onboarding flow: questions about the learner, a path summary, assessment review, and clearer roadmap guidance. The newest inspected source-file save time is `services/learning_service.py` on **14 Oct, 15:24**, with assessment/follow-up UI files saved shortly beforehand. File timestamps are clues only: they cannot prove the exact sequence of conversations or which assistant made each edit.

## 3. Before, pushed version, and local version

| Area | Original baseline | Pushed `25810af` | Current local work | Assessment |
|---|---|---|---|---|
| Coding languages | Python execution; other topics generally use written exercises | Python, Node.js JavaScript, SQLite SQL runners | Explicit language can influence lesson/exercise generation | 🟡 Runners exist, but JS/SQL have correctness gaps below. |
| Code editor | Python-oriented | Python / JavaScript / SQL highlighting | Same editor, updated lesson presentation | ✅ Language selection is connected to the editor. |
| Grading feedback | Existing score/failure display | Expanded test result presentation | Assessment credit hidden from exercise feedback unless there was a real attempt | 🟡 Must distinguish platform failures from learner failures consistently. |
| Waiting for lessons | Existing loading behavior | Added skeleton interface | Skeleton integrated into session view | ✅ UI implementation present. |
| Before roadmap creation | Goal goes directly to orchestration | Same | Follow-up agent and step-by-step questions | 🟡 Answers are collected, but most are not used by the planner. |
| Learning scope | Inferred from goal | Same | `focused` / `foundation` instructions | 🔴 UI sends uncontrolled text; backend expects exact values. |
| Language consistency | Overall goal passed to lesson content | More languages can be graded | `primary_language` passed to creative lesson and exercise generation | 🟡 Learner's chosen answer is not reliably converted into this field; worked examples do not receive it directly. |
| Subjects beyond coding | Some conceptual topics possible | Format selector still oriented around coding | Prompts now mention music, finance, cooking, writing, etc. | 🟡 Academic, GitHub, job-market and foundation assumptions are still largely technical. |
| Roadmap arrival | Opens session page directly | Same | New path summary modal before entering session | ✅ UI present; needs browser coverage. |
| Diagnostic quiz | Gives a head-start, not mastery | Same | “Don't know” choices and answer review screen | 🔴 Answer IDs and repeat-submission state need fixes. |
| Where to begin | Available nodes are clickable | Same | “Start Here” badge | ✅ Useful UI guidance; not a new adaptive scheduling engine. |
| Database outage | Database errors surface | Same | Repository catches errors and falls back to memory | 🔴 A successful response can now mean progress was not saved durably. |
| Database startup | Alembic setup documented | Same | Automatic `create_all()` | 🟡 Does not replace migrations or install the vector extension. |
| Generation progress stream | Already implemented | Already implemented | Existing stream now carries additional request fields | ✅ This was inherited, not newly introduced after the push. |

### Files involved in the work after the push

| Group | Modified tracked files | New, untracked files |
|---|---|---|
| Agents | `agents/consensus/consensus_agent.py`, `agents/content_generator/content_agent.py`, `agents/exercise/exercise_synthesizer_agent.py`, `agents/exercise/format_selector_agent.py` | `agents/followup/__init__.py`, `followup_agent.py`, `followup_schema.py` |
| Backend API | `api/main.py`, `api/routes.py`, `api/schemas.py` | — |
| Workflow wiring | `graph/content_graph.py`, `graph/exercise_graph.py`, `graph/learning_plan_graph.py`, `graph/lesson_graph.py` | — |
| Saving and learning state | `persistence/repository.py`, `services/learning_service.py` | — |
| AI instructions | Assessment, consensus, content generator, example generator, exercise synthesizer, orchestrator, and scout query prompts | `prompts/followup_prompt.py` |
| Dashboard and session UI | `web/app/page.tsx`, `web/app/session/[sessionId]/page.tsx`, `web/app/globals.css` | — |
| UI components | `AssessmentQuiz.tsx`, `LessonPanel.tsx`, `SkillGraph.tsx` | `AssessmentResults.tsx`, `FollowUpModal.tsx`, `PathSummaryModal.tsx` |
| Browser/API contracts | `web/lib/api.ts`, `web/lib/status.ts`, `web/lib/types.ts` | — |
| Tests | `tests/test_api.py`: two mocks adapted to accept extra arguments | No new test files for the new features. |
| Local handover/sample | — | `RESOURCES_AND_WORKFLOW.md`, `session.json` |

`CODE_AUDIT.md` is deliberately ignored by Git. This new report is a separate file. Treat `session.json` as a local session/sample artifact until its contents have been reviewed; do not include it blindly in a commit.

## 4. How the application works today

```mermaid
flowchart TD
    A["Enter an email<br/>Local browser identity"] --> B["Enter a learning goal"]
    B --> C["NEW: AI asks follow-up questions"]
    C --> D["Send goal + preferences<br/>Scope and detected language"]
    D --> E{"What kind of request?"}
    E -->|Full path| F["Scout splits research<br/>Academic + Market + Practical"]
    F --> G["Consensus builds skill roadmap<br/>Reviewer checks it"]
    G --> H["Save session<br/>NEW: show path summary"]
    H --> I["Diagnostic quiz<br/>NEW: answer review"]
    I --> J["Interactive roadmap<br/>NEW: Start Here badge"]
    J --> K["Open an unlocked skill"]
    K --> L["Write lesson + gather facts<br/>Add example and diagram"]
    K --> M["Prepare exercise + grading tests"]
    L --> N["Read, practice, ask tutor"]
    M --> N
    N --> O["Submit answer and grade"]
    O --> P{"Score?"}
    P -->|80 or more| Q["Master skill<br/>Unlock dependants"]
    P -->|40 to 79| R["Keep practicing<br/>No structural change"]
    P -->|Below 40| S["Add remedial prerequisites<br/>Lock failed skill until ready"]
    Q --> J
    R --> J
    S --> J
    E -->|Single lesson or exercise| T["Backend generates a direct response<br/>Dashboard currently shows only a notice"]
```

| Part | Everyday explanation | Actual implementation |
|---|---|---|
| Web interface | The learner's desk: roadmap, lesson, editor, quiz, tutor | Next.js 15 / React 19, React Flow, Monaco, TanStack Query |
| API | Reception desk forwarding actions to the correct service | FastAPI, `api/routes.py` |
| Workflow graphs | A checklist coordinating specialists in the right order | LangGraph: learning plan, lesson, content, exercise |
| AI providers | The writers/reviewers behind generated material | Groq key pool with local Ollama fallback; model choices configurable |
| Evidence gathering | Sources that help support AI-generated material | DuckDuckGo; Apify job postings; GitHub repository metadata; uploaded PDF chunks |
| Progress database | The learner's saved notebook | PostgreSQL session JSON; explicitly selected memory mode for temporary development |
| PDF search | Finding relevant passages from uploaded study notes | PyMuPDF, FastEmbed, per-user memory or pgvector store |
| Grading runner | Checks whether submitted code meets generated tests | Python subprocess, Node.js runner, SQLite runner |
| Adaptive rules | Decides whether to unlock, retry, or add basics | Deterministic 80/40 thresholds plus AI proposals for remedial skills |

**Two important corrections:** An Academic agent response is a university-style curriculum generated by an LLM; the agent does not actually fetch university syllabi. GitHub grounding uses repository search metadata, not the contents of repository files. Search-backed generation also does not guarantee that every sentence is factually verified.

### The actual score rules

| Score/result | Learner sees/gets | Roadmap behavior |
|---|---|---|
| 80–100 | Mastered | Dependants unlock when their other prerequisites are complete. |
| 40–79 | In progress | Retry the skill; no remedial expansion for this range. |
| Below 40 | Needs review | Remediation lock is set; AI proposes prerequisite skills. |
| Broken grading tests | Not graded | Existing harness-error path avoids a mastery penalty; this needs consistent coverage across all languages. |
| Correct diagnostic MCQ | Head-start score 60 | In progress, **not mastered**; the real exercise is still required. |
| A worse retry after mastery | Mastery should remain | `apply_score()` preserves prior mastery; the new assessment update currently bypasses that protection. |

The older `CODE_AUDIT.md` uses **70/50** in its diagrams. Those values are incorrect for this checkout. The source constants are **80/40** in [services/mastery.py](services/mastery.py).

## 5. Where personalization currently breaks down

```mermaid
flowchart LR
    A["Learner chooses<br/>experience, language, goal, scope"] --> B["Preferences stored<br/>on session"]
    B -. "No general preference text<br/>passed into planner prompts" .-> C["Scout and specialists<br/>still mainly read original goal"]
    A --> D["Language field uses<br/>AI detection from original goal"]
    D --> E["May ignore learner's<br/>actual language answer"]
    A --> F["Scope sent as display text"]
    F --> G["Backend only understands<br/>focused / foundation"]
    style E fill:#ffd6d6,color:#111
    style G fill:#ffd6d6,color:#111
```

| Example | Intended behavior | What the source currently does |
|---|---|---|
| “Learn programming”, then choose JavaScript | Roadmap, examples and exercise use JavaScript | Sends `detected_language` from the original query; does not map the learner's language answer into that field. |
| Choose “Focused path” | Produce only relevant specialist topics | Uses `answers.scope`; question IDs are unconstrained and option values are human-readable strings. Only exact `focused` activates the focused instruction. |
| Say “I am experienced; I have two weeks” | Adjust starting depth and amount of work | Answers are saved but not generally passed into Scout/Academic/Consensus prompts. |
| “Learn guitar” without a scope question | Start with music fundamentals | UI defaults to `foundation`; that backend instruction explicitly requests computer-science foundations. |
| Click “Skip All” | Continue using sensible defaults | Closes the modal and does not start generation. |
| Follow-up agent returns an empty question list | Continue or show a recoverable error | Modal reads `current.id` from an undefined question and can crash. Schema does not require a nonempty list. |

## 6. Concrete flaws to fix in the current implementation

**Priority guide:** **P0** = fix before trusting grades, saving, or public access; **P1** = fix before calling the current feature set complete; **P2** = improve clarity and repeatability. Evidence marked **source** was established by reading the execution path, not by running the whole application.

| ID / priority | Finding and plain-language impact | Evidence / location | Proposed correction |
|---|---|---|---|
| **F01 · P0** | **SQL exercises with multiple assertions cannot be graded.** The validator accepts individual JSON objects, then grading joins them with newlines. The runner expects one JSON document, so two or more objects fail parsing. | Reproduced with the actual Python executor: one assertion scored 100%; two assertions passed validation but returned `harness_error=true`, with no score. `agents/exercise/grader_agent.py:138`; `tools/code_executor.py:511,609`. | Define one structured assertion list; serialize it as one JSON array. Use the same representation in validation and execution. |
| **F02 · P0** | **JavaScript can award credit to an unfinished or failing asynchronous test.** The runner calls a test without waiting, increments the pass count, and the caller ignores the process exit code. | Standalone execution of the actual embedded runner: a passing sync test, failing async assertion, and never-settling async test each printed `MINDMORPH_SUMMARY 1 1`. See `tools/code_executor.py:392`. | Await test completion; use one reliable runner path; check exit status; reject incomplete results. Keep timeout and platform-error handling separate. |
| **F03 · P0** | **An empty JavaScript test module can earn 100%.** Fallback `node --test` counts the test file itself as a passing test even when it contains no exercise assertions. | Standalone execution of `module.exports = {}` printed `MINDMORPH_SUMMARY 1 1`. | Require actual discovered exercise tests; never award a grade for “the file ran successfully”. Reject empty/zero-test artifacts. |
| **F04 · P0** | **Database failure can look like successful saving.** A failed save goes into process memory. After a restart it disappears; after database recovery it may be hidden or replaced by an older database version. Multiple workers do not share the fallback. | Forced-outage reproduction: `repo.save()` returned normally and state existed only in the memory fallback. `persistence/repository.py:56–139`, newly changed local behavior. No real database was touched. | Keep temporary memory mode explicit. In durable mode, return a clear unavailable/save error rather than silently switching storage. If recovery buffering is required later, design it as durable, visible storage. |
| **F05 · P1** | **Follow-up answers are mostly collected, rather than applied.** Experience, goals, and arbitrary preferences do not shape the research prompts. Language and scope conversion is unreliable. | Source: `web/app/page.tsx:117–124`; `graph/learning_plan_graph.py:48,240–254`; `services/learning_service.py:263`. | Introduce a validated learner profile with stable field names and machine-readable options; pass it to research, consensus, lessons, examples and exercises. |
| **F06 · P1** | **“Universal learning” still takes a coding-shaped route.** Music/business goals can receive CS foundations, GitHub project searches, and coding-oriented practical material. Selector failure also defaults to a Python challenge. | Source: foundation instruction in `graph/learning_plan_graph.py:245`; exercise fallback in `graph/exercise_graph.py:77`; existing practical/academic/market prompts. | Route research by subject; make foundations domain-specific; choose a safe subject-appropriate exercise when selection fails. Do not claim broad-domain support complete until representative journeys pass. |
| **F07 · P1** | **Assessment result IDs are missing.** Backend stores `q.get('id')`, but quiz questions have `node_id`, not `id`. Frontend searches using `node_id` and falls back to array position, hiding the mismatch. | Source: `services/learning_service.py:546`; `agents/assessment/assessment_schema.py`; `web/components/AssessmentResults.tsx:72`. | Pick a consistent question identifier, store and type it on both sides, and match by it. Add separate question IDs if multiple questions per skill are supported. |
| **F08 · P1** | **Resubmitting the diagnostic can downgrade a mastered skill.** It unconditionally sets `status='in_progress'`, even if `best_score` is already 100. There is no submitted-state rejection. Completion reads status, so progress can go backward. | Actual service reproduction changed `mastered` → `in_progress` while retaining `best_score=100`, and stored a null question ID. `services/learning_service.py:526–570`; contrast with sticky mastery in `services/mastery.py`. | Make assessment submission idempotent or reject repeats; preserve mastered state; keep diagnostic credit separate from exercise attempts. |
| **F09 · P1** | **Follow-up failure/skip paths are incomplete.** “Skip All” cancels instead of continuing; empty questions can crash; initial AI failure blocks path creation with a generic API message. | Source: `web/app/page.tsx:94–108,231`; `web/components/FollowUpModal.tsx:54–58,239`; unconstrained follow-up schema. | Continue without questions on skip; validate counts, options and default index; offer retry or explicit continuation on analysis failure. |
| **F10 · P1** | **A bad roadmap can remain impossible to finish.** Unknown edges are pruned, but duplicate IDs, self-dependencies and cycles are not comprehensively rejected. Cycle guards prevent recursion, not permanent locking. Reviewer rejection is displayed but does not repair the graph. | Actual merge accepted `A → B → A`; completion rules then locked both available skills. `graph/skill_graph_adapt.py`, `services/completion.py`, graph schema and reviewer node. | Validate unique IDs, allowed levels, edges and acyclicity after initial generation and adaptation. Retry/repair within a bound, then return a recoverable error. |
| **F11 · P1** | **The grade endpoint does not recheck the lesson lock.** Opening a lesson is gated, but a previously cached lesson can still be submitted directly after graph changes lock it. | Source: `services/learning_service.py:344` vs `:461`; grade checks for cached lesson only. | Recheck eligibility at grade time and reject locked or outdated exercise submissions; tie submissions to a specific exercise revision. |
| **F12 · P1** | **Two actions can overwrite each other's progress.** Chat, grading and lesson opening read then replace the entire saved session. Overlapping actions can save stale copies. | Source: load → mutate → save routes in `api/routes.py`; JSONB upsert in repository. | Add session revision checks with conflict handling, or serialize mutations per session using a database-backed mechanism. Test overlapping requests. |
| **F13 · P1** | **The app can say healthy while the database is unusable.** Startup catches DB errors; `/health` still returns `ok`. Automatic table creation also skips migration history and the explicit vector-extension step. Database connection attempts have no explicit application timeout. | Source: `api/main.py:46–56`; `persistence/db.py:create_all`; vector migration. Test collection was observed waiting inside `psycopg.connect`; a two-second connection timeout allowed the suite to run. | Separate running/readiness checks; run Alembic deliberately; bound connection attempts and report dependency failures clearly. |
| **F14 · P1** | **Language consistency relies too much on AI obedience.** Creative content receives the explicit language, but worked examples do not. Standalone content/exercise routes omit the new language fields. Generated artifact language is not checked against the selected language before grading. | Source: `graph/content_graph.py:125,147`; top-level content/exercise nodes; `GraderAgent._validate_tests` vs `grade_submission`. | Normalize language aliases; propagate the profile consistently; enforce agreement among exercise, artifact, editor and runner; define exceptions for database skills within a JS path. |
| **F15 · P1** | **Single lessons/exercises are generated but not shown.** A non-roadmap response becomes a dashboard notice and its actual content/exercise is dropped. | Source: dashboard `onDone` in `web/app/page.tsx`; backend response contains `final_content` / `exercise`. | Display the returned lesson/exercise or explicitly make the screen roadmap-only. Add browser tests for each supported route. |
| **F16 · P2** | **Assessment wording promises skipping that the code does not do.** Correct MCQs give score 60 and do not unlock dependants; the UI says known material will not need repeating. Review display also cannot be reopened after refresh through the current UI. | Source: `AssessmentQuiz.tsx`, `AssessmentResults.tsx:45`, session page `showResults` state. | Say “head-start” consistently, or deliberately design a tested skip policy. Add “Review assessment” using the persisted results. |
| **F17 · P2** | **New overlays need keyboard/accessibility work.** They lack dialog semantics and managed focus; small-screen overflow also needs checking. | Source: `FollowUpModal.tsx`, `PathSummaryModal.tsx`. | Add dialog names/roles, focus entry/trapping/return, deliberate Escape behavior, and scrollable content. Verify with keyboard and narrow viewport. |
| **F18 · P1** | **New behavior has little dedicated regression coverage.** The pushed change added no test files; local test changes only loosen two API mocks. Some existing mock call assertions still expect the old content-agent arguments. Dashboard browser tests expect immediate navigation and do not handle the new modals. | Source: commit/diff; `tests/test_content_graph.py`; `web/tests/e2e.spec.ts:98–114`. | Add tests at the behavior boundaries below; update existing expectations to the new product contract. Do not fix the suite merely by removing assertions. |
| **F19 · P2** | **Windows paths leak into learner-facing grading failures.** The cleaner recognizes Unix-style paths but leaves the Windows temp-directory path in feedback. | Actual failure: `test_missing_name_failure_has_no_absolute_path` in `tests/test_code_executor.py`. Regex in `tools/code_executor.py:48` only handles slash-separated paths. | Normalize/redact paths for Windows and Unix, retaining the helpful filename/error. Add coverage for both forms. |
| **F20 · P2** | **“Most recent first” can be ambiguous for very fast saves.** Memory storage sorts only by wall-clock timestamp. Tied timestamps preserve the older insertion order. | `test_list_sessions_scoped_per_user_recent_first` failed in this Windows run. A fixed-clock reproduction saved `s1` then `s2` and returned `s1` first. `persistence/repository.py:159–176`; no tie-break field exists. | Make the ordering contract deterministic, using a save sequence or defined tie-break; test tied timestamps deliberately. |
| **F21 · P2** | **A fresh Python install is not reproducible.** Most requirements have no version bounds or lock, so two setups can resolve different AI/framework versions. | A fresh Python 3.11 setup installed 102 packages from the current declarations. No Python lockfile was found. This does not by itself explain the failures below. | Record and lock a tested dependency set; test clean installs in CI alongside upgrades. |

### Public-service prerequisites, if “live” means accessible by other people

These are relevant to the actual code paths in this repository. Whether the deployed service has additional protection is **not verified**.

| Existing gap | What it means for a public service | Required before public trust |
|---|---|---|
| Email/localStorage identity; caller-supplied `user_id` | Knowing/changing an ID can select another learner's sessions and uploaded-material namespace. An email field is not authentication. | Authenticate requests; derive user identity on the server; check ownership on every session/upload route. |
| Grading runs submitted code in host subprocesses | A timeout is not isolation. Submitted code can access host files/network. Environment filtering also does not exclude a credential-bearing `DATABASE_URL`. | Move execution into an isolated worker with explicit environment allowlist, filesystem/network restrictions, and resource limits. |
| PDF upload reads the full request; no application size limit | Oversized documents can consume memory and expensive embedding work. Proxy limits, if any, are unknown. | Enforce upload/page/chunk limits, bounded processing, and resource/rate controls. |
| No request quotas or long-task queue | Repeated generation/grading requests can exhaust compute and provider capacity. | Add per-user limits, bounded concurrency and job timeouts appropriate to the deployment. |

These are release blockers for a public multi-user service, rather than reasons to build every infrastructure item from the target architecture now.

### Why silent database fallback is especially risky

```mermaid
flowchart TD
    A["Database becomes unavailable"] --> B["Save catches error"]
    B --> C["Progress saved only in process memory"]
    C --> D["API still returns success"]
    D --> E{"What happens next?"}
    E -->|Process restarts| F["Temporary progress disappears"]
    E -->|Database recovers| G["Database reads bypass fallback<br/>Newer progress can disappear from view"]
    E -->|Different worker handles request| H["Worker has a different memory store"]
    style D fill:#fff3bf,color:#111
    style F fill:#ffd6d6,color:#111
    style G fill:#ffd6d6,color:#111
    style H fill:#ffd6d6,color:#111
```

## 7. What remains according to IMPLEMENTATION_STATUS.md?

| Roadmap area | Code reality today | Work still required | Do now? |
|---|---|---|---
| P0: orchestration foundation | LangGraph workflows and specialist fan-in exist | Structural roadmap validation and deliberate reviewer-failure behavior | **Yes**, as current correctness work. |
| P1: exercise and grading | Python/JS/SQL implementation exists | Fix SQL serialization; JS async/empty-test scoring; validate exercise contracts | **Yes, first.** |
| P1: adaptive learning | Mastery, locks and remedial graph expansion exist | Guard cycles, stale submissions, failed remediation and persistent state | **Yes.** |
| P1: database/backend | PostgreSQL repository, API and migrations exist | Remove misleading fallback; prove restart/recovery; prevent lost updates | **Yes.** |
| P1 #7: provider chain and retrieval | Groq/Ollama and PDF/seed retrieval exist | Other vendors, reranking, exercise retrieval improvements | Later; first prove current fallback behavior. |
| P2 #8: onboarding/assessment | Diagnostic plus local follow-up/results screens | Use actual learner answers; fix IDs/state; align wording and domains | **Yes.** |
| P2 #9: material ingestion | PDF → embeddings → per-user retrieval exists | Limits and durability validation now; OCR, per-session scoping and large-scale indexing later | Split immediate safety/correctness from expansion. |
| P3 #10: tutor | Text streaming with lesson/PDF context exists | Concurrency/reconnect checks now; voice and multiple threads later | Fix current reliability first. |
| P3 #11: screen vision/browser agents | Not found in implementation | Entire new capability | Later. |
| P3 #12: frontend | Next.js product and scratchpad exist; new onboarding screens local | Finish modal flows, direct responses, keyboard/mobile checks and regression tests | **Yes.** |
| P3 #13: security/infra | DB compose and test workflow exist; real auth/isolated execution absent | Public access prerequisites first; larger hosting/analytics architecture as needed | Conditional public-release priority. |
| Cost and timing | Lesson-compose token accounting and in-process stage spans exist | All-agent cost coverage, per-user aggregation, exporters/dashboards | Later unless needed to diagnose current reliability. |
| Evaluation | Offline groundedness judge exists | Grading correctness dataset, prompt/domain regression cases and CI wiring | Add targeted grading/domain cases now; expand later. |

**CrewAI remains a stated non-goal.** LangGraph already provides the workflow coordination used here. Redis, Kafka, Kubernetes, Terraform, Pinecone, voice, and screen vision are not prerequisites for fixing the present learning loop.

### Documents that now need reconciliation

| Current claim | Correction needed |
|---|---|
| Implementation status says grading executes Python only | JS and SQL execution code was added in `25810af`; mark it implemented but needing the correctness fixes identified here. |
| Implementation status says no CI/CD | A test CI workflow exists at `.github/workflows/tests.yml`. A deployment pipeline is still unverified/absent from the inspected files. |
| Test counts say 268 Python passes / 30 browser passes | Historical counts, not this audit's results. Current checkout has 33 Python test files and 7 browser spec files, with 41 discovered frontend checks. |
| Implementation status says assessment credit goes through `apply_score` | Local code now directly updates node state; this change introduced the repeat-submission/sticky-mastery issue. |
| Old audit diagrams use mastery 70 and review 50 | Correct to 80 and 40. |
| Handover calls execution secure and implies strict language alignment is complete | Subprocess execution is not a sandbox, and the explicit preference path is incomplete. |
| Handover describes SQL fixtures being prepared by the platform | Runner currently executes the learner's statements in an empty SQLite database. No separate platform fixture/setup field exists. Query-only SQL exercises need a proper setup/query contract. |
| Handover describes progress streaming as newly added after push | Streaming endpoints and browser stream handling already existed in the baseline. |
| Broad subject support appears complete in new wording | Most changes are prompts and UI; domain-aware research/exercise behavior remains partial. |

## 8. Verification performed during this audit

<!-- AUDIT_VERIFICATION_START -->
| Check | Result | What it proves / limit |
|---|---|---|
| Git status, history, reflog, branch comparison | ✅ Reviewed | Establishes inherited baseline, pushed commit, and local changes. |
| Read-only GitHub branch query | ✅ Feature `25810af`; main `0cc8c13` | Verifies remote branch heads without pulling into the dirty working tree. |
| Frontend `npm run typecheck` | ✅ Passed | Current TypeScript source passes the configured compiler checks. |
| Audit document diagrams | ✅ All **6** Mermaid diagrams parsed | Checked with the installed Mermaid renderer in headless Chromium. |
| Frontend suite after installing Chromium | 🔴 **37 passed / 4 failed**, 41 total | Ran against a temporary source copy on port 3102. Two stale navigation-flow tests and two stale accessible-name selectors failed. See breakdown below. |
| Clean production build | ✅ Passed, Next.js **15.5.19** | Temporary source copy and existing installed Node dependencies; compilation, type validation and page generation completed. No application `.env` files copied. |
| Backend pytest in isolated Python **3.11.17** | 🔴 **288 passed / 13 failed / 3 skipped**, 304 total | Completed in 117.35 seconds. In-memory mode, AI endpoint disabled, unavailable DB with explicit `connect_timeout=2`. Three real-DB checks skipped. |
| Actual JS executor + embedded-runner probes | 🔴 False grades confirmed | Failing async and empty test cases returned **100%** through `execute_javascript`; unresolved async test also produced a 1/1 pass summary in the embedded runner. |
| Actual SQL validator/executor probes | 🔴 Contract mismatch confirmed | Two assertions passed validation, then failed execution with `harness_error`; single assertion control passed at 100%. |
| Actual assessment service probe | 🔴 State/ID defects confirmed | Repeated assessment changed mastered status to in-progress; stored `question_id` was null. |
| Mocked DB outage + actual graph merge probes | 🔴 Fallback/cycle behavior confirmed | Failed database save silently used memory; cyclic adaptation was accepted and left both skills locked. |
| Follow-up schema probe | 🔴 Empty list accepted | Confirms backend validation permits the empty input that the modal cannot handle. |
| Deployed app, real providers and real PostgreSQL | ❓ Not verified | No deployment revision/durability claim is made. |

**Setup performed with user authorization:** Installed Chromium for Playwright, a managed Python 3.11 runtime, and an ignored project `.venv` containing declared requirements. Conda was unavailable, so tests used `.venv/Scripts/python.exe`. The `uv` bootstrap helper lives in a temporary folder; `py-spy` was added to the test environment to diagnose the stalled database check. Application code, `.env` files and existing server configuration were not edited. No Git pull, commit, push or deployment was performed.

The initial frontend attempt had 17 missing-browser launch failures; after installing Chromium those were resolved. The initial build hit a lock/permission error in the existing `.next` directory; the clean isolated build passed. Those initial tooling failures should not be counted as additional application defects.
<!-- AUDIT_VERIFICATION_END -->

### What the failing tests actually mean

| Group | Count | Explanation | Next action |
|---|---:|---|---|
| Python content/consensus mock arguments | 6 | Tests expect calls without the newly introduced `primary_language` or `scope_instruction` keyword. | Update assertions to the intended profile contract and check the actual values. |
| Python RAG/upload fake content agents | 3 | Test doubles reject the new `primary_language` argument. This blocks the tests before retrieval behavior is assessed. | Update test-double interfaces and rerun the retrieval checks. |
| Python exercise-selection mocks | 2 | Mocks do not set `language`; a `MagicMock` value reaches the new supported-language check and switches format to case study. | Use real schema-shaped mock outputs; add separate language/fallback behavior tests. |
| Python Windows path redaction | 1 | Real Windows temp path survives feedback cleanup. | Fix F19 and retain the assertion. |
| Python recent-session ordering | 1 | Rapid saves did not produce the expected newest-first order. Timestamp ties are not defined. | Fix/verify F20 with an explicit equal-time test. |
| Browser dashboard-to-session journeys | 2 | Tests assume immediate navigation, but the new follow-up/summary flow is not mocked or handled. | Mock follow-up responses and interact through both modals. |
| Browser keyboard Enter/Space tests | 2 | Selector expects `Python Basics. Available`; actual accessible name is `Start Here. Python Basics. Available`. | Update selectors while continuing to verify keyboard activation works. |

These suite failures are separate from the serious grading defects discovered by direct probes. A green suite after updating mocks would still need new regression tests for those defects.

**Repeat the backend checks in a separate PowerShell terminal** using the environment prepared during this audit:

```powershell
# From the repository root. These settings affect this terminal, not .env files.
$env:MINDMORPH_STORE = "memory"
$env:GROQ_API_KEYS = ""
$env:MINDMORPH_LLM_PROVIDER = "ollama"
$env:MINDMORPH_OLLAMA_HOST = "http://127.0.0.1:1"
$env:DATABASE_URL = "postgresql+psycopg://mindmorph:mindmorph@127.0.0.1:1/mindmorph?connect_timeout=2"
$env:PGCONNECT_TIMEOUT = "2"
$env:LANGSMITH_TRACING = "false"
$env:LANGCHAIN_TRACING_V2 = "false"
./.venv/Scripts/python.exe -m pytest -q --tb=short
```

Frontend checks are `npm run typecheck`, `npm run build`, and `npx playwright test --timeout=60000 --workers=2` from `web/`. Run the build separately from the dev server/tests so they do not compete for `.next`. The audit used temporary source copies to avoid the existing port 3000 server and output lock. Database integration checks require a separate PostgreSQL/pgvector setup; the memory-mode run above deliberately does not prove durability.

## 9. Fix and improvement plan — finish the current scope first

```mermaid
flowchart LR
    A["1. Accurate grades<br/>SQL + JS + artifact validation"] --> B["2. Reliable saving<br/>DB errors + revisions + restart"]
    B --> C["3. Real personalization<br/>Stable learner profile + domain routing"]
    C --> D["4. Consistent progress<br/>Assessment + locks + graph validation"]
    D --> E["5. Complete learner flow<br/>Skip + results + direct responses + access"]
    E --> F["6. Verify and release<br/>Regression suite + real DB + smoke checks"]
```

| Step | Work package | Related findings | Finish when |
|---|---|---|---|
| 0 | Protect the current work; review sample artifacts; confirm deployed branch/revision. If public, prioritize auth and grading isolation. | Public-service prerequisites | Local changes are backed up intentionally; live revision and exposure are known. |
| 1 | Correct SQL list serialization; await JS tests; reject empty/incomplete harnesses; enforce language and artifact contracts. Define SQL fixture vs learner-query behavior. | F01–F03, F14 | Good solutions pass, wrong solutions fail, and broken platform tests never punish learners in all three languages. |
| 2 | Keep memory mode explicit; remove hidden durable-store fallback; add readiness/migration checks; introduce session revisions/conflict handling. | F04, F12, F13 | Restart, database outage/recovery and overlapping actions preserve or clearly reject progress changes. |
| 3 | Create one validated learner profile; normalize options/languages; apply experience, goal and scope throughout generation; route non-coding subjects appropriately. | F05, F06, F14 | Changing an answer changes the relevant planner input; guitar does not get CS foundations; a JS preference reaches examples/editor/runner coherently. |
| 4 | Fix assessment identifiers; preserve mastery; make repeat submission safe; validate graph IDs/edges/cycles; recheck lock and exercise revision on grading. | F07, F08, F10, F11 | Repeated actions cannot downgrade completion or bypass learning rules; every accepted graph has a route to completion. |
| 5 | Finish skip/failure/empty-follow-up flow, direct response display, assessment review reopening, honest wording and accessible/mobile modals. | F09, F15–F17 | Every visible action has the advertised outcome, including error/recovery paths. |
| 6 | Add targeted regression tests, update stale existing tests and docs, fix Windows path cleanup/session ordering, lock tested Python dependencies, run CI, then verify the deployed revision. | F18–F21 and document drift | All agreed checks pass with evidence; remaining limitations are explicitly documented. |

These should be separate, reviewable changes. Do not mix a grading correction, storage redesign and new product feature into one large commit.

## 10. Practical testing plan

### A. Grading correctness — highest priority

| Test | Expected result | Level |
|---|---|---|
| Python / JS / SQL: correct solution | Accurate nonzero test count and correct score | Real runner, no LLM |
| Same exercises: deliberately wrong solution | Failed tests and correct partial/zero score | Real runner |
| JS async passing test | Pass only after awaited completion | Real runner |
| JS async failing / rejected promise | Fail, never a false 100% | Real runner |
| JS unresolved promise / infinite loop | Bounded timeout; no leaked child process | Real runner, Windows and Linux |
| Empty JS file / no exercise tests | Platform harness error; no mastery change | Runner + service |
| SQL one assertion / multiple assertions | Both parse and execute through the same contract | Validator → grader → runner |
| SQL fixture + learner SELECT | Grade learner results against seeded data, rather than requiring invented setup | New agreed SQL contract |
| Malformed generated tests / missing runtime / wrong artifact language | Clear “not graded”; attempts, mastery and adaptation unchanged | Runner + service + API |
| Grade feedback displayed | Score corresponds to the submitted exercise revision | Browser + API |

### B. Personalization and learner flow

| Journey | Expected result |
|---|---|
| Vague programming goal → learner chooses JavaScript | Stored profile and prompts use that choice, not only initial detection. |
| Focused vs foundation | Stable option values reach the backend and generate different relevant instructions. |
| MERN path → database skill | Explicitly test the intended policy for SQL/database exercises within a JavaScript-oriented path. |
| Guitar / finance / cooking | Appropriate research sources, domain foundations and written/practical exercises; no automatic Python/CS detour. |
| Follow-up service fails / empty questions / invalid default | Retry or continue safely, without a crashed page. |
| “Skip All” | Continues generation with defaults. |
| Summary → quiz → results → roadmap | Each screen appears in sequence and navigation works. |
| Answer wrong / no idea / skip quiz | Correct explanation and truthful feedback; no undeserved mastery. |
| Refresh after results | Saved assessment remains available for review. |
| Request a single lesson / exercise | Actual generated response is visible. |
| Keyboard-only and narrow screen | Modal focus is contained/returned, controls are named, and content is reachable. |

### C. Progress and persistence

| Scenario | Expected result |
|---|---|
| Score 39 / 40 / 79 / 80 | Exactly the intended remediation / retry / mastery boundary. |
| Master skill, then resubmit diagnostic or worse exercise | Prior mastery remains intact. |
| Duplicate assessment request | Safe replay or explicit conflict; no duplicate credit/event drift. |
| Grade a locked cached lesson / stale exercise | Server rejects the submission deliberately. |
| LLM returns a cycle / duplicate ID / self-edge / nonexistent edge | Reject or repair before serving; graph stays finishable. |
| Adaptation fails, then learner retries recovery | Clear bounded recovery; no permanently trapped path. |
| Open lesson twice | Reuses cached lesson and cost accounting stays correct. |
| Save → stop API → restart → reload | Same grade, lessons, assessment results and graph remain in real PostgreSQL. |
| Database down while grading | No false claim that progress was saved. |
| Database recovers / request reaches another worker | No disappearing fallback-only progress. |
| Tutor reply overlaps grading or lesson opening | Both changes survive; stale saves do not overwrite newer ones. |
| Upload PDF as learner A, retrieve as A and B | A can retrieve it; B cannot. Ownership must be authenticated for public use. |

### D. Run order and release gates

```mermaid
flowchart TD
    A["Runner and service tests"] --> B["API tests with stubbed AI"]
    B --> C["Type check + isolated production build"]
    C --> D["Browser tests with mocked API"]
    D --> E["Real PostgreSQL<br/>Migrations + restart + concurrency"]
    E --> F["Small live AI sample<br/>Python + JS + SQL + non-coding"]
    F --> G["Check deployed revision<br/>Repeat core journey on live service"]
    G --> H["Current implementation ready<br/>Begin next tasks"]
```

Mocked tests prove product rules without relying on AI wording. Real-runner tests prove grading. Real-database tests prove saving. A small live AI sample checks whether generated statements/tests are usable together. Passing one layer does not replace the others.

| Release checklist | Required evidence |
|---|---|
| Grades are trustworthy | No false passes for async/empty JS tests; multiple SQL assertions work; broken harnesses never change mastery. |
| Progress is trustworthy | Durable restart proof, outage behavior and conflict tests pass. |
| Personalization is real | Profile fields are validated and reach the correct generation stages. |
| Learning rules are consistent | Score boundaries, repeat assessments, locks and graph integrity pass. |
| New screens work | Skip/error paths, summary/quiz/results/roadmap flow, keyboard and mobile checks pass. |
| Existing behavior stays intact | Updated regression suite passes; historical test totals are replaced by actual current results. |
| Public exposure is controlled | Authentication/ownership and isolated code execution verified before public multi-user use. |
| Documentation and release agree | Status document, setup commands, runtime versions, commit and deployed revision match. |

## 11. First practical batch after this audit

1. Fix **F01–F03** and add real Python/JS/SQL grading regression cases.
2. Fix **F04** so durable saves cannot silently become temporary.
3. Fix **F07–F09**: consistent assessment IDs, preserved mastery, safe follow-up skip/failure handling.
4. Wire the learner profile properly, then test one programming and one non-programming journey.
5. Complete the graph/lock/concurrency checks, update the implementation status, and review the release gates above.

The sections above preserve the original audit and proposed corrections. The approved grading repair
and its verification results are recorded in the implementation update at the beginning of this file.
