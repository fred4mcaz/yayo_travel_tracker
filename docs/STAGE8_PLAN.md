# Stage 8 — Gmail ingest

The plan for the biggest remaining gap. Read [HANDOFF.md](HANDOFF.md) first for the
domain model; this document assumes it.

**Built offline-first.** Every phase below is testable with no Google App
Password and no Anthropic API key. Those two secrets are needed only to *switch
it on* (Phase 6), and they go into `deploy/.env` on the server by hand — never
pasted into chat.

---

## The line that must not move

> **Nothing writes to trip data without an explicit accept.**

Extractions land in `extraction` with `status=pending` and appear in a review
queue. Accepting is the only code path that may create or modify a `Trip`,
`Stay`, `Leg`, or `CountryEntry`. This has been stated twice and is tested for
directly in Phase 4.

Two more constraints inherited from the design:

- **No historical backfill.** The first run records a watermark and ingests
  nothing. "Going forward only" was chosen deliberately.
- **The local pre-filter is a privacy control, not a cost optimisation.** Most
  of his inbox must never leave the box. A leaky filter is a data leak, not an
  overspend.

---

## Decisions taken before writing code

**The `anthropic` SDK pin must move.** `anthropic==0.42.0` is pinned in
`backend/requirements.txt`. Its `ToolParam` carries exactly four fields —
`input_schema`, `name`, `cache_control`, `description`. There is no `strict`,
so the strict tool schema this design depends on cannot be expressed. Relying
on unknown dict keys surviving serialisation is the kind of thing that works on
the dev box and fails quietly in the container. The pin moves in Phase 3.

**Models are his explicit choice, kept.** `claude-haiku-4-5` to triage,
`claude-sonnet-5` to extract. Both support strict tool use. Noted for the
record: the API reference's own default is `claude-opus-4-8`, and choosing a
cheaper model is the user's call. Pricing per Mtok at time of writing —
Haiku 4.5 `$1/$5`; Sonnet 5 `$3/$15`, with introductory `$2/$10` through
**2026-08-31**.

---

## Phase 1 — IMAP fetch and the watermark ✅

**Objective.** Pull new mail into `email_message` and never pull it twice. No
LLM, no network in tests.

**Done.** `backend/app/services/email_ingest.py` and
`backend/app/services/settings.py`, covered by
`backend/tests/test_email_ingest.py` (10 tests). Suite is 91 passing.

**Assumptions to validate.**
- `imap-tools` can fetch by `UID > n` against a UID-ordered mailbox.
- `imap_uid` plus a unique `message_id` is sufficient to dedupe.
- The watermark needs storage that survives a run which ingests zero rows, so
  it cannot be derived from `max(email_message.imap_uid)`.

**Risks acknowledged.**
- The watermark *is* the no-backfill guarantee. Get it wrong on first run and
  the entire inbox is pulled. Tested directly.
- Watermark and rows must move together. A watermark stored outside the
  database can desync from the rows on a crash; keep it transactional.
- Gmail UIDs are per-folder and reset if `UIDVALIDITY` changes. Store
  `UIDVALIDITY` alongside the watermark and re-baseline rather than re-ingest.

**Exit tests.**
- First run against a populated fake mailbox stores a watermark and ingests
  **zero** messages.
- Second run ingests only UIDs above the watermark.
- Running twice over the same mailbox is idempotent.
- A changed `UIDVALIDITY` re-baselines instead of re-ingesting.

---

## Phase 2 — The local pre-filter ✅

**Objective.** Decide `looks_like_travel` on the box, before anything leaves it.

**Done.** `backend/app/services/email_filter.py` reading
`data/rules/email-filter.json`, wired into the ingester's store path, covered by
`backend/tests/test_email_filter.py`. Suite is 114 passing.

**Assumptions to validate.** Sender and keyword rules are expressible as
committed read-only data under `data/rules/` (the directory exists and is empty
for exactly this).

**Risks acknowledged.** This is the privacy boundary. A false positive sends
personal mail to a third party.

**Exit tests.** A fixture set of ordinary personal mail scores
`looks_like_travel=False` and is provably never handed to the extractor;
booking-confirmation fixtures pass.

---

## Phase 3 — Extraction behind a strict tool schema ✅

**Objective.** Turn a candidate email into a pending `Extraction`.

**Done.** `backend/app/services/extraction.py` (triage → extract funnel, strict
tool schemas, validation, `AnthropicModel`), covered by
`backend/tests/test_extraction.py` (20 tests). Suite is 134 passing.

**Assumptions to validate.** The SDK bump lands clean on the 1.9 GB box;
`strict: true` with `additionalProperties: false` and a full `required` list
yields tool inputs that validate exactly.

**Risks acknowledged.** Prompt and response drift; per-message token cost.
Bounded by fixtures and by triaging with Haiku before extracting with Sonnet.

**Exit tests.** An injected fake client returning canned tool calls produces
`Extraction` rows with `status=pending`; a malformed model response is rejected
rather than persisted; no network is touched.

---

## Phase 4 — Matching and the accept boundary ✅

**Objective.** Propose the right destination, and make accepting the only way
in.

**Done.** `backend/app/services/review.py` (`find_matching_trip`, `suggest`,
`accept_extraction`, `reject_extraction`), covered by
`backend/tests/test_review.py` (18 tests). Suite is 152 passing.

**Assumptions to validate.** A ±2 day date overlap attaches to an existing
trip. An extraction for a *different country* proposes a **new trip** — the
one-country rule means it must not fail against `_guard_single_country`.

**Risks acknowledged.** This is the line above. Everything here is about
keeping it.

**Exit tests.** Accepting is the only path that mutates trip data; a Thailand
extraction against a Vietnam trip proposes a new trip rather than returning
409; rejecting leaves trip data untouched.

---

## Phase 5 — Review queue UI ✅

**Objective.** See what was proposed, accept or reject it.

**Done.** `backend/app/api/review.py` (list/count/accept/reject, registered on
the protected routers), `frontend/src/views/Review.tsx` with a badged tab in
`App.tsx`, review styles in `styles.css`. Covered by
`backend/tests/test_review_api.py` (12 tests) plus the service tests. Suite is
165 passing, frontend builds clean, and the whole flow was driven in a real
browser against a throwaway DB.

**Risks acknowledged.** Every input bug in this project was found by driving
the real UI, never by reading code. Verified in a browser, not by inspection.

**Exit tests.** Accept and reject driven through the real interface against a
local session; the trip list reflects an accepted extraction.

---

## Phase 6 — Scheduler and gating

**Objective.** Run it every 10 minutes, off by default.

**Assumptions to validate.** APScheduler survives the container's restart
policy; the flag genuinely gates the poller.

**Risks acknowledged.** With `YAYO_EMAIL_INGEST_ENABLED=false` the scheduler
must not start, must not connect, and must not log a credential.

**Exit tests.** Flag off — no scheduler, no connection attempt. Flag on with
absent credentials — a clear startup failure, not a silent no-op.

**Then, and only then:** he adds the Google App Password (needs 2FA on
`req4233@gmail.com`) and the Anthropic API key to `deploy/.env` on the server
himself.

---

## Lessons learned

Updated at each phase boundary.

### Phase 1

- **No migration was needed.** `Setting` already exists in `models.py` — a
  key/value table whose docstring names "last IMAP UID" as its first use. The
  assumption that the watermark needed new storage was wrong; it needed a key.
  The watermark lives at `imap_last_uid`, the folder incarnation beside it at
  `imap_uidvalidity`, both written in the same transaction as the rows.

- **`UID n:*` does not mean "above n".** In an IMAP range the two endpoints are
  an *unordered pair*, so when the folder's highest UID is below `n` the server
  returns that highest message anyway. Filtering server-side is not enough:
  without a client-side `uid <= watermark` guard, every poll of a quiet mailbox
  re-stores the same message forever. The fake mailbox in the tests reproduces
  this deliberately, so the guard cannot be removed without a test failing.

- **The watermark advances mid-loop, so ordering is load-bearing.** A mailbox
  yielding UIDs out of order would strand the lower ones below the mark
  permanently. `fetch_after` documents an ascending contract and the ingester
  stops the batch rather than trusting it — stranded mail is silent, a short
  batch is not.

- **Identity is Message-ID, not UID.** A folder move reissues UIDs, and the
  same message under a new UID would otherwise be extracted twice.

- **`imap-tools` 1.7.4 has no `uidvalidity` attribute.** `UIDVALIDITY` and
  `UIDNEXT` both come from `mailbox.folder.status()`. Baseline is `UIDNEXT - 1`
  so an empty folder baselines at 0 instead of failing.

- **The pre-existing `F401` in `alembic/.../ba5dfa0e58f9_*.py` is now fixed**
  (unused `import sqlmodel` removed; `sa` is still used in `downgrade`).
  `ruff check .` over all of `backend/` is clean.

### Phase 2

- **The rule is asymmetric on purpose and the tests lead with refusals.** A
  friend forwarding a perfect booking confirmation is *refused* — an unlisted
  sender can never be promoted by keywords. That is the control working. The
  first tests in the file are the leak cases (unlisted sender, lookalike
  domain, forwarded booking), because those are what a bug here would expose.

- **Domain matching needs the dot.** `endswith(allowed)` lets
  `notbooking.com` satisfy a `booking.com` rule; the check is `domain == a or
  domain.endswith("." + a)`. `booking.com.phish.ru` and `mybooking.com` are
  both refused by tests.

- **`parseaddr` was checked, not guessed** — and it surprised twice. `garbage`
  (no `@`) parses to domain `"garbage"`; `a@b@c.com` fails RFC parsing to
  empty. Both match nothing, so both are safe refusals, but the test
  expectations had to be corrected to the real values rather than my
  assumptions.

- **Empty local part was a real hole.** `@booking.com` parses to a valid
  allowed domain and was let through. Not a spoofing concern (the filter trusts
  domains by design), but no legitimate sender has an empty local part, so
  `classify` now requires both halves of the address. Cheap, strictly safer.

- **Denials are subject-only, keywords are subject-or-body.** Real
  confirmations carry a newsletter/unsubscribe footer in the body; matching
  denials there would veto the mail we want. A confirmation keyword in the body
  still counts, because some senders put nothing useful in the subject.

- **Commentary lives in the JSON.** Keys prefixed `_` (`_comment`,
  `_deny_comment`) hold the rationale so the rule file explains itself; the
  loader ignores them and a test asserts none leak in as domains.

### Phase 3

- **The SDK pin moved 0.42.0 → 0.120.2, pinned exactly.** Latest at the time.
  0.42's `ToolParam` had four fields; 0.120's has `strict` (and
  `allowed_callers`, `defer_loading`, `input_examples`, `eager_input_streaming`,
  `type`). It pulls in `docstring-parser` as a new transitive dependency — the
  container rebuild in Phase 6 needs a working index, which it has. The full
  suite passed unchanged after the bump; webauthn is a separate package and was
  untouched.

- **Strict mode is expressed by nullability, not omission.** Strict tool use
  requires every property in `required` and `additionalProperties: false`, so
  optional fields are typed `["string", "null"]` and the model must decide each
  one. This is stricter in spirit than the old "leave it out" style — the model
  cannot silently skip a field.

- **The API's guarantee is still validated on our side.** Strict mode
  guarantees shape *from the real API*, but a refusal, a fake, or a future
  schema change might not, so `validate_booking` re-checks everything: country
  is 2 alpha chars, dates parse via `date.fromisoformat`, confidence in [0,1].
  A payload with neither a country nor a date is rejected as unmatchable — phase
  4 could never place it.

- **The mock transport goes through `http_client`, not `transport`.**
  `anthropic.Anthropic(transport=...)` is a `TypeError`; the seam is
  `http_client=httpx.Client(transport=httpx.MockTransport(handler))`. Two tests
  push the real strict tool schema out over that mock and read the `tool_use`
  back, so the wire shape is exercised with no socket.

- **Triage gates extraction, and a test proves Sonnet is not spent when Haiku
  says no.** `model.extracted == []` after a `False` triage. The funnel is the
  cost control the two-model split exists for.

- **Every processed email is marked, pass or fail.** A message that triages out
  or yields an unusable extraction still gets `processed_at` set, so it is not
  re-sent on every 10-minute poll. Only a *valid* booking creates an
  `Extraction`; malformed results persist nothing.

### Phase 4

- **The one-country rule is upheld by construction, not by catching an error.**
  A different-country booking is filtered out at *match* time
  (`find_matching_trip` skips a trip whose known country differs), so its
  suggestion is `None` and accept creates a new trip. It never reaches
  `_guard_single_country` with a mismatch on the happy path. The guard is still
  there as defence in depth for a stale suggestion (trip's country changed after
  matching), and both paths are tested.

- **The invariant is a row count, not a claim.** `test_matching_and_rejecting_
  never_write_trip_data` snapshots `(trips, stays, legs)` and asserts suggest
  and reject leave them untouched; only accept moves them. That is the strongest
  form of "accepting is the only writer" available.

- **Accept goes through the same derived-state path a manual edit does** —
  `refresh_trip_dates` then `sync_country_entries` from `services.trips`, so an
  accepted booking and a typed one cannot diverge. Tests assert the span
  refreshes and a `CountryEntry` is synced.

- **`Extraction.email_message_id` is a real foreign key.** The first test pass
  failed with `FOREIGN KEY constraint failed` because the fixture invented an
  id. The builder now creates a throwaway `EmailMessage` first. The pure
  matching tests (no Extraction row) passed throughout, which localised it fast.

- **Matching reads the denormalised trip span**, which already folds in the
  leaving date — so a stay that ends at "leaving on" rather than the last
  checkout still matches a late-arriving booking. Slack is ±2 days, tested at
  the boundary (2 days in matches, 3 days out does not).

- **Accept is deliberately strict about incomplete hotels.** A hotel booking
  with no city or no check-in raises `NotAcceptable` rather than inventing a
  `Stay` — the reviewer edits it first (phase 5). Legs have no required date
  fields, so they accept freely.

### Phase 5

- **A failed accept was creating an orphan trip.** The first API test caught it:
  `accept_extraction` created the new `Trip` *before* `_apply_hotel` raised on a
  missing city, leaving an empty trip behind — a write on a *failed* accept,
  which the invariant forbids. Fixed by moving all refusals into
  `_check_acceptable`, run before any trip is created. Locked in by
  `test_a_failed_accept_leaves_no_orphan_trip`. This is exactly the kind of bug
  the row-count invariant tests exist to surface.

- **Overrides are allow-listed.** The accept endpoint takes a partial booking so
  the reviewer can fix a missing field, but only booking fields pass through
  (`ALLOWED_OVERRIDES`); a stray `trip_id` or `suggested_trip_id` in the body is
  ignored, not honoured. Tested.

- **The suggestion is freshened at read time.** A trip added after extraction
  may now be the right home, so `GET /api/review` re-runs the (cheap, read-only)
  match before serialising. Verified in the browser: a proposal created before
  its trip existed still showed "Joins …" once the trip was there.

- **read_page reports the placeholder as the input's accessible name.** All four
  Country inputs showed "e.g. VN" in the a11y tree while the flags rendered the
  right country — looked like a binding bug. The DOM said otherwise: the values
  were "TH"/"JP"/"TH"/"VN". This is the handoff's "trust the DOM over the
  rendering" lesson in a new form; always check `input.value` via JS, not the
  a11y name.

- **The one-character input bug did not recur.** Typing "Tokyo" into the review
  city field landed all five characters (checked via `input.value`). The Review
  form uses the shared `Fields` primitives, not `Sheet`, so it never had the
  autofocus-steals-caret effect — but it was worth confirming.

- **The committed `launch.json` backend entry was broken.** `runtimeExecutable`
  was `backend/.venv/...` with `cwd: backend`, which the preview tool resolves to
  `backend/backend/.venv/...` → ENOENT. Fixed to the cwd-relative
  `.venv/Scripts/python.exe`. This also fixes it for the user, not just the
  preview tool.

- **Browser verification used a throwaway DB.** A gitignored `backend/.env`
  pointed `YAYO_VAR_DIR` at a scratch dir; migrations + a seed script + a minted
  session there. The real `var/travel.db` was never touched (same size and
  mtime after). The `.env`, scratch DB, and seed script were all removed
  afterwards. Repeat this pattern for any future UI verification — do not seed
  the real dev DB.

- **Auth skip: set the cookie value with JS even though it is `httponly`.** The
  server sets `yayo_session` httponly, but httponly only blocks JS from
  *reading* it — a JS-set cookie of the same name is still sent, and the server
  reads by value. `document.cookie = "yayo_session=<token>; path=/"` +
  `create_session` is the whole skip.
