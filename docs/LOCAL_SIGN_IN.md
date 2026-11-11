# Local password sign-in

The frontend now uses the backend's existing JWT authentication. Open
`http://localhost:3000`, choose **Create an account** once, then sign in using
that account's email and password. Registration requires at least eight password
characters. Failed sign-in displays the backend's error and does not authenticate.

The API assigns the user ID; entering an email or editing the old local identity
does not grant access to a learner's records. API ownership checks remain in place.

## Credentials and requests

The access token, server-assigned user ID and email are retained in this tab's
session storage. Passwords are not stored. Refresh retains sign-in; closing the
tab normally ends the browser session. If browser storage is unavailable, sign-in
can continue in memory until refresh.

JSON requests, grading, Run/cancellation, session/tutor streams and multipart PDF
uploads attach the Bearer token. Uploads retain the browser-generated multipart
boundary. An authenticated 401 clears the current credentials and returns to
sign-in. A delayed failure from an earlier account cannot sign out a newer account.
Sign-out clears cached server data and credentials, while saved answer drafts remain.

## Existing email-only learning records

Earlier versions stored paths and documents under a local email-only ID. Those
rows and saved drafts are preserved. They are **not automatically assigned to a
new account**: signing up with an email alone does not establish ownership of
older private records. Linking legacy data requires an explicit operator-approved
account mapping. No automatic ownership transfer or public claim endpoint was added.

## Database and local restart

The pre-existing authentication migration creates the `users` table. A database
still at `0006_grading_records` must apply the current migration head before the
current API starts. Back up an existing database first; then use the normal
`python -m alembic upgrade head` procedure. This requirement comes from the existing
authentication branch; BKT adds no database columns or tables.

The local restart on 12 November 2026 validated a backup, applied the existing
authentication/merge migrations, enabled `MINDMORPH_BKT=1`, and verified that all
four existing paths and six document chunks were retained. A real authenticated
HTTP/browser smoke used only a temporary synthetic account, then removed its
records. It verified grading evidence, persisted BKT displays, refresh and logout
without model-generated exercises or AI calls. Private verification artifacts are
under the ignored `logs/bkt-runtime/` folder.

Browser regressions cover password login, registration, invalid credentials,
expired sessions, account switching, draft retention, authenticated PDF uploads,
and the existing learning/grading/streaming flows. Fixture credentials are explicit
test data; production never accepts a localStorage email as authentication.

Verification after restart: 67 focused backend/authentication/BKT checks passed.
The full browser run passed 74 scenarios; three editor-helper failures were fixed
and all three passed on rerun, covering all 77 scenarios. Type checking and the
isolated production build passed. The real HTTP/browser smoke also passed and
confirmed that original learner rows were unchanged after removing its test account.

The runtime smoke observed connection/time-out warnings from the existing optional
Kafka exporter. PostgreSQL grading and BKT persistence completed, but an unavailable
configured broker can delay event emission. Kafka configuration was not changed.
The final capability check reported 13 of 14 profiles available; `react-dom`
(React/TSX execution) remained unavailable under the versioned worker checks.
No worker verification was bypassed or configuration changed for that profile.
