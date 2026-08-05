# Immigration readiness — implementation plan

## What this adds

For every trip, tell the traveller **whether they are ready to cross the
border**: are they eligible to enter on the passport they'll carry, do they
need a **visa** (or e‑visa / visa‑on‑arrival / ETA), and do they need to submit
an **arrival card** (a.k.a. entry card / disembarkation card)? Show that status
across all trips, and let the email checker confirm an arrival card (or visa
approval) when the confirmation lands in Gmail.

Worked example (the one asked for): a trip to **Indonesia** on the US passport
should read *"Visa‑on‑arrival required · Arrival card required — not yet
confirmed"*, and flip the arrival‑card line to *confirmed* once the e‑customs /
arrival‑card email arrives.

## Decisions locked with the user (2026‑08‑04)

1. **Policy source = the LLM, at runtime — but cached.** We ask the model "what
   does an `{MX|US}` passport holder need to enter `{country}`", and **cache the
   answer per (country, nationality)** so it is *not* a call on every page load.
   Advisory only, with a visible "checked on <date> — border rules change,
   verify" line. No refresh mechanism (see decision 7) — cached forever once
   fetched. This replaces the never‑built static `entry-requirements.json` /
   `visa-free.json` idea in README §7/§2.
2. **Passport: default US.** Readiness is computed for the trip's **selected**
   passport; when none is chosen it **defaults to the US** passport. If a trip
   selects the **MX** passport, readiness recomputes for MX on that trip.
3. **Loud discrepancy flag.** If an immigration email records a
   passport/nationality that **differs** from the passport selected on the trip,
   flag it **loudly** on the trip (not a quiet muted note).
4. **Email: local match, keep on box** — a *separate* local classifier flags
   immigration mail (government/immigration senders + immigration keywords) and
   **never sends it to the LLM automatically**. The 3‑day **recent‑emails**
   review list additionally lets the user pick an *unflagged* immigration email
   and send **that one** for LLM extraction (explicit per‑message consent — the
   existing D2 manual‑extract flow, extended to immigration documents).
5. **Storage: materialize checklist rows.** Auto‑create `Requirement` rows per
   trip from the policy; an email confirmation flips one to `submitted`/
   `approved`. Reuses the existing `Requirement` table, CRUD, and Paperwork UI.

## Design principles carried over from the existing app

- **The accept boundary is sacred (README §5).** *Nothing writes trip data
  without an explicit accept.* Immigration confirmations therefore land as
  **proposals in the Review queue** and only flip a requirement to confirmed on
  accept — they never auto‑write. `sync_requirements` (materializing the
  *checklist*, not the *confirmation*) is derived state on the same footing as
  `sync_country_entries`, so it may run automatically; the **confirmation** is
  what waits for a human.
- **The privacy boundary is sacred (README §5).** The immigration classifier is
  a second local filter; immigration mail stays on the box unless the user picks
  it for extraction. The **policy** LLM call is a generic factual query (no
  personal email content), so it is outside that boundary — but it still costs,
  hence the cache.
- **Quiet on the past** (like missing‑travel, README §1): readiness is surfaced
  for **upcoming / ongoing** trips; past trips stay silent.
- **Advisory, never authoritative.** Border policy changes without notice; every
  surface says so. We never *block* on readiness — worst case it reads "unknown".

## Data model touchpoints (already present unless noted)

- `Requirement(kind ∈ {entry_card, visa, eta, insurance, vaccination,
  onward_ticket, custom}, status ∈ {todo, submitted, approved, not_required},
  reference, due_date, note)` — **exists, full CRUD, renders read‑only today.**
  *Add:* `source` (Actor: `system` | `manual` | `email`) so `sync_requirements`
  only ever manages its own auto‑created rows and never clobbers a hand‑added or
  email‑confirmed one.
- `CountryEntry.permit_type` / `permitted_days` / `passport_id` — **exists.**
- `EntryPolicy` — **new** cache table: `(country_code, nationality)` unique →
  `permit_type`, `permitted_days`, `arrival_card_required`, `arrival_card_name`,
  `visa_required`, `eta_required`, `insurance_required`, `vaccination_required`,
  `onward_ticket_required`, `summary`, `advisory`, `source_model`, `fetched_at`.
  Written once per pair, never updated (decision 7 — no refresh path).
- `EmailMessage.looks_like_immigration` — **new** bool, the local immigration
  flag (sibling of `looks_like_travel`).

---

## Phase 0 — Schema & migrations

**Why:** every later phase reads or writes these columns/tables; landing them
first keeps each subsequent phase a pure feature slice with no migration noise.

**Assumptions to validate**
- SQLite can add these columns/tables via Alembic the way the existing 5
  migrations do (they run on container start).
- `Requirement.source` can be added `NOT NULL` only with a `server_default`
  (README §6 trap — SQLite cannot add NOT NULL without one). Default `system`.

**Gotchas / risks**
- Empty‑dir / `.gitkeep` traps don't apply (no new dirs).
- `EntryPolicy` unique constraint on `(country_code, nationality)` — mirror the
  `MergeDismissal` unordered‑pair pattern for the constraint declaration.
- Times: `fetched_at` is a real instant → `utcnow()` (README models.py note).

**Tasks**
- [x] Add `EntryPolicy` table to `models.py` (+ enums reused, no new enum).
- [x] Add `Requirement.source: Actor` — defaults `manual` (every row created
      before this column existed was typed by hand); `sync_requirements`
      (Phase 2) stamps `system` on the rows it materialises.
- [x] Add `EmailMessage.looks_like_immigration: bool` (default `False`, indexed).
- [x] One Alembic migration (`b119ab960928`) creating the table + two columns,
      with explicit `server_default` on both new NOT NULL columns (the SQLite
      trap from README §6 — autogenerate misses this, added by hand).
- [x] `schemas.py`: confirmed — nothing new needed yet, existing Requirement
      schemas already cover every field; `source` is system-managed, not
      user-settable via the API.

**Tests that must pass to proceed**
- [x] `pytest backend/tests` green (262 passed, was 257) — new
      `tests/test_immigration_models.py` round-trips `EntryPolicy`, checks its
      unique constraint, and asserts the two new columns' defaults.
- [x] Migration applies cleanly against a copy of the real prod DB snapshot
      (`var/travel.db`, which had rows in `email_message`): upgrade, downgrade,
      re-upgrade all clean; `alembic check` reports no drift from the models;
      existing rows backfilled `looks_like_immigration=0` correctly.
- [x] `ruff check app tests` clean.

**Notes for the next engineer**
- Migration chain: `... -> 3f166b5b4e6d (learned_rule) -> b119ab960928
  (immigration readiness)`. Generated via `alembic revision --autogenerate`
  against a scratch `YAYO_VAR_DIR`, then hand-edited to add `server_default`
  to the two new NOT NULL columns on existing tables — autogenerate does not
  add these itself, and without them SQLite refuses the migration on a
  populated table (same trap `c0d08c15146c` hit for `leg.country_code`).
- To rehearse a migration against a local copy of prod before deploying: copy
  `var/travel.db` into a scratch `YAYO_VAR_DIR` and run `alembic upgrade head`
  there — see the shell history in this phase for the exact commands. The real
  `var/` is never touched by this rehearsal.

Commit: `886d7c5`

---

## Phase 1 — Entry‑policy service (LLM‑backed, cached, graceful)

**Why:** the "brain" that answers *what do I need for country X on nationality
Y*. Everything visible downstream is a rendering of this. Isolating it behind
the same model‑Protocol seam as extraction keeps the whole thing testable
offline and the cost controlled.

**Assumptions to validate**
- The existing `OpenRouterModel` seam can host a third call (`assess_entry_policy`)
  alongside `triage`/`extract` without disturbing them.
- A strict tool schema can return the permit type + arrival‑card flags reliably.

**Gotchas / risks**
- **Cost/hallucination:** cache hard, permanently, by `(country, nationality)` —
  once a row exists it is never re‑queried (decision 7: no refresh mechanism at
  all). Surface `fetched_at` as a "checked on" date so staleness is visible even
  though nothing re‑fetches it. A small in‑request memo still avoids two calls
  for the same pair inside one trip‑detail load before the cache row commits.
- **Unconfigured box:** no OpenRouter key ⇒ `get_policy` returns `None`
  ("unknown"), the UI degrades to advisory‑unknown, nothing errors.
- **Validation:** re‑validate the tool output (`permit_type ∈ PermitType`,
  `permitted_days` sane, booleans real) exactly like `validate_booking` — trust
  nothing from the model. Ask about **every** `RequirementKind` (visa,
  entry_card, eta, insurance, vaccination, onward_ticket) in one call so a
  later "oh also insurance" isn't a second round‑trip — but only the kinds the
  model marks required are cached as "required" (decision 8).

**Tasks**
- [x] `services/entry_policy.py`: `ENTRY_POLICY_TOOL` (strict), an
      `EntryPolicyModel` Protocol + `OpenRouterPolicyModel` implementation
      (its own client class, deliberately separate from `extraction.py`'s —
      it asks a generic factual question, not email content, so the two
      concerns stayed decoupled rather than overloading one class for both),
      `validate_policy`, and `get_policy(session, country_code, nationality,
      model=None)` — read‑through cache, queries the model only on a genuine
      cache miss, no refresh parameter at all.
- [x] Config: `YAYO_POLICY_MODEL` default `anthropic/claude-sonnet-5`
      (`backend/app/config.py`), gated only by `openrouter_api_key` being set
      — not by `email_ingest_enabled`, since this never touches mail.

**Tests that must pass to proceed**
- [x] Fake `EntryPolicyModel` → `get_policy` caches (second call makes zero
      model calls — asserted directly on the fake's call log).
- [x] Unconfigured (`model=None`) ⇒ `get_policy` returns `None`, no exception,
      no row persisted.
- [x] `validate_policy` drops a bad `permit_type`, an out‑of‑range
      `permitted_days`, and a non‑dict payload; accepts a `visa_free` reading
      with every "required" flag false.
- [x] Cache is scoped per `(country_code, nationality)` — US and MX for the
      same country each trigger their own model call and persist their own row;
      lowercase/uppercase country codes hit the same cached row.
- [x] Real `OpenRouterPolicyModel` against a mocked HTTP transport: the strict
      tool goes out on the wire, the nationality and country name land in the
      prompt, and the round trip through `get_policy` persists a row with
      `source_model` recorded.
- [x] `pytest backend/tests` green (276 passed, was 262); `ruff check` clean.

**Notes for the next engineer**
- `RequirementKind` has 7 members (`entry_card, visa, eta, insurance,
  vaccination, onward_ticket, custom`); the policy tool asks about the 6
  border‑crossing ones (`custom` is always user‑authored, never policy‑driven)
  in a single strict call. `visa_required`/`entry_card_required`/etc. are
  independent booleans, not a `permit_type`‑derived guess — Phase 2's
  `sync_requirements` reads them directly rather than re‑deriving "does this
  permit type imply a visa row".
- `get_policy` takes the model as an explicit parameter rather than building
  one internally, so Phase 2's call sites decide whether an unconfigured box
  means "skip readiness" (pass `model=None`) or "fetch now"
  (`OpenRouterPolicyModel.from_settings()`), matching how `extraction.py`
  separates the pure pipeline from its `from_settings()` constructor.

Commit: `c80d768`

---

## Phase 2 — Materialize requirements + derive readiness (backend)

**Why:** turn a policy into the per‑trip checklist and the compact status the
list and detail views show. This is the load‑bearing derived‑state layer.

**Assumptions to validate**
- Readiness passport = `CountryEntry.passport` nationality **or US** when unset,
  per decision 2.
- Auto‑managed rows are safely reconcilable when the passport flips (US visa‑free
  → MX visa‑required must add a `visa` row; the reverse must retire it) **without
  ever touching** a `manual`/`email` row or a status the user changed.

**Gotchas / risks**
- **Never clobber user/email state.** `sync_requirements` only creates/updates/
  deletes `source == system` rows whose status is still `todo`; a `system` row the
  user advanced (or an email confirmed) is left as‑is even if the policy would no
  longer create it (keeps history honest, avoids yanking a confirmed card away).
- **Idempotency:** running it twice changes nothing. Call sites:
  `sync_country_entries` path (trip mutation) and `update_entry` (passport
  change), mirroring how dates are refreshed.
- **Undated / no‑country trips:** no policy, no rows, silent.
- **Suppress the "nice to have" kinds when not required (decision 8).**
  `sync_requirements` only creates a row for a kind the cached policy marks
  required. `insurance`, `vaccination`, `onward_ticket` are asked about but
  produce **no row at all** — not a "not_required" row — when the policy says
  they don't apply, so the Paperwork section never shows placeholder noise for
  them. `visa` and `entry_card` follow the same rule but are the ones expected
  to actually fire most of the time.

**Tasks**
- [x] `services/entry_policy.py`: `readiness_passport(entry) -> Nationality`
      (US default, MX only when the trip's `CountryEntry.passport` says so).
      Also added `cached_policy` (the read‑through cache's lookup half, made
      public so `trip_readiness`'s alternate‑passport hint can peek without
      ever fetching) and `policy_model_or_none()` (the from‑settings seam
      Phase 2's call sites use, mirroring `scheduler.py`'s credential gate but
      degrading to `None` instead of raising).
- [x] `services/trips.py`: `sync_requirements(session, trip, model=None)`
      (materialize/reconcile `system` rows from the policy) and
      `trip_readiness(session, trip)` → `{ state: ready|action|unknown|na,
      passport, is_default_us, permit, permitted_days,
      checklist:[{kind,label,status}], arrival_card, advisory, checked_on,
      alternate_passport_hint }`.
- [x] Wire `sync_requirements` into the trip mutation (`_after_change`) +
      passport‑change (`update_entry`) paths in `api/trips.py`, via
      `policy_model_or_none()`.
- [x] Add `readiness` (compact: state/permit/permitted_days/arrival_card/
      checked_on) to `list_trips` payload and (full) to `trip_detail` payload.

**Tests that must pass to proceed**
- [x] US → Japan: visa‑free, **no** `visa` row; readiness `ready` pre‑arrival‑card.
- [x] US → Indonesia: `visa` (VoA) + `entry_card` rows, state `action`.
- [x] Passport US→MX flip reconciles rows; a user‑advanced status survives.
- [x] Undated/no‑country trip → readiness `na`, no rows.
- [x] Bonus, not in the original list but load‑bearing: an *unknown* policy
      (no cache, no model) leaves existing rows untouched rather than reading
      silence as "nothing required"; a row the user/email advanced past `todo`
      survives reconciliation even when the policy would no longer create it;
      the two API-level tests (`create stay` / `list trips`) prove
      `sync_requirements` and the compact/full readiness split are actually
      wired into the routes, not just reachable as bare functions.
- [x] `pytest backend/tests` green (284 passed, was 276); `ruff check` clean.

**Notes for the next engineer**
- `policy` being `None` inside `sync_requirements`/`trip_readiness` means "we
  don't know yet", never "nothing required" — an unconfigured box or a
  not‑yet‑cached pair must not retract or suppress rows. This is the one
  branch it would be easy to get backwards during a future refactor; the
  `test_unknown_policy_leaves_existing_rows_untouched` test in
  `test_immigration_readiness.py` guards it directly.
- `readiness["state"]` is `"ready"` both when every required item is settled
  *and* when nothing is required at all (empty checklist) — visa‑free Japan
  with no arrival card reads `ready`, not `na`. `na` is reserved for "nothing
  to assess" (no country / undated), `unknown` for "would assess, but no
  policy reading exists yet".
- `alternate_passport_hint` is cache‑only by design (see its docstring) — it
  never triggers a second model call just to maybe show a hint, so it stays
  silent until some other trip happens to have already cached the other
  nationality's policy for the same country. No test pins its exact wording
  since Phase 2's required test list didn't call for one; Phase 3 should
  decide the copy when it renders it.
- `list_trips`'s compact readiness reuses the same `trip_readiness()` call as
  `trip_detail`'s full one and just filters keys client-side in the route —
  there is no separate "compact" computation path to keep in sync.

Commit: `a7e30a9`

---

## Phase 3 — Readiness UI (frontend)

**Why:** the whole point — "display the immigration readiness status of all
trips" and the per‑trip detail.

**Assumptions to validate**
- The trip card has room for a compact badge; the detail panel's read‑only
  "Paperwork" section is the right place to grow into "Immigration readiness".

**Gotchas / risks**
- Month‑first dates, `parseDate`, 16px inputs, `key={selected.id}` remount rules
  (README §3/§6) all apply to any new controls.
- Keep past trips quiet (gate on status like the missing‑travel banner).
- No Refresh control anywhere (decision 7) — the "checked <date>" line is
  informational only, not actionable.
- Only render checklist rows that exist (decision 8) — `insurance` /
  `vaccination` / `onward_ticket` simply won't appear when not required,
  because no row was materialized for them; nothing to filter client‑side.

**Tasks**
- [x] `types.ts`: `Readiness`/`ReadinessSummary`/`ReadinessChecklistItem`/
      `ArrivalCardReading`/`ReadinessState` + `readiness` on `TripSummary`
      (compact) and `TripDetail` (full, overriding narrower→wider per TS
      interface-extension rules); `source: Actor` on `Requirement`.
- [x] Trip card (`Trips.tsx`): compact readiness badge (✅ ready / ⚠️ action /
      ❔ unknown) with the permit one‑liner, via the new
      `lib/immigration.ts#readinessBadge` shared with the detail panel so the
      two never describe the same state differently. `na` renders nothing.
- [x] `TripDetail.tsx`: "Immigration readiness" section — permit summary, the
      US‑default note / MX‑selected, **alternate‑passport hint** ("US would be
      visa‑free"), the checklist with the existing status `<select>`, arrival‑card
      confirmed indicator, an empty slot commented for Phase 5's loud
      discrepancy banner, and the advisory + "checked <date>" line (no Refresh
      button). The generic Paperwork section now filters out whatever the
      readiness checklist already rendered, so `system`-sourced immigration
      rows don't appear twice.
- [x] vitest for the badge state mapping (`lib/immigration.test.ts`, 6 cases).

**Tests that must pass to proceed**
- [x] `npm test` green incl. new badge test (42 passed, was 36).
- [x] Browser (local, no passkey): verified end-to-end against a real trip in
      the local dev DB (after applying the Phase 0 migration there, which had
      not yet been run locally). With no policy cached: card badge reads "❔
      Not checked yet" and the detail section reads "Not checked yet for a MX
      passport." With a policy manually seeded to simulate a fetched
      visa‑on‑arrival/entry‑card reading (removed again after): card badge
      read "⚠️ Visa on arrival · 30 days · arrival card not yet confirmed",
      the detail section showed the Visa + Arrival card ("Indonesia e-CD")
      rows with working status `<select>`s; approving the arrival card row
      dropped the "not yet confirmed" note from the badge; flipping the
      passport to US (uncached) recomputed the section to "unknown" *without*
      deleting the MX‑sourced rows, which then correctly reappeared under the
      generic Paperwork section (the never‑clobber + graceful‑fallback rules
      both proven live, not just in tests). Text-based verification
      (`get_page_text`/`read_page`) rather than a pixel screenshot — the
      Browser pane wasn't displayed for compositing in this session; the
      transcript above is the evidence trail. All test mutations were reverted
      (deleted the seeded `entry_policy` row and the two `Requirement` rows,
      restored the passport selection) — the local dev DB carries no lasting
      change beyond the additive schema migration.

**Notes for the next engineer**
- The local dev DB (`var/travel.db`, i.e. the file `backend/app/config.py`'s
  `var_dir` default resolves to at the repo root) had **not** had the Phase 0
  migration applied before this phase — `alembic upgrade head` was required
  before `/api/trips` would even load locally. If you hit `no such table:
  entry_policy` locally, that's why.
- `trip_readiness`'s "ready" state actually means "checklist empty or fully
  settled," and the checklist is read from whatever `Requirement` rows
  already exist for the trip — it does **not** call `sync_requirements`
  itself. A trip whose country's policy becomes cached later (fetched by a
  different trip for the same country+nationality) will not pick up new
  checklist rows until *this* trip is itself mutated again (a hotel/leg edit
  or a passport change) and `sync_requirements` runs. This was directly
  observed during manual verification and is consistent with how
  `sync_country_entries`/`refresh_trip_dates` already behave (derived state
  materialises on mutation, not retroactively) — worth knowing before
  assuming a "ready" badge always means "policy confirms nothing is owed."
- An unrelated stray `python.exe` (PID varies) was already bound to port 8000
  on this machine, running a stale build of the backend (missing even the
  pre‑existing `arrival_mode` field) — left alone since it wasn't started by
  this session and its origin is unknown. Verification here ran the backend
  on port 8010 instead, with `frontend/vite.config.ts`'s proxy target
  temporarily pointed there and reverted afterward (`git diff` was clean
  before committing).

Commit: `d451581`

---

## Phase 4 — Local immigration email classifier + confirmation proposals

**Why:** "look for immigration‑related emails that arrive" and "show if an
arrival card has been confirmed via email" — the privacy‑preserving local path.

**Assumptions to validate**
- Government/immigration senders can be enumerated for the countries he visits
  (ID, TH TDAC, SG ICA, US CBP/ESTA, PH, IN, etc.) and extended over time like
  the booking allow‑list.
- A local classifier + country/date match is enough to *propose* a confirmation;
  the LLM is not needed for the common case.

**Gotchas / risks**
- Keep the accept boundary: the classifier creates a **proposal**, accept flips
  the `entry_card` requirement to `submitted`/`approved` + stores `reference`.
- Matching reuses the `_overlaps` / country logic from `review.py`.
- Best‑effort nationality sniff from subject/snippet feeds the discrepancy flag
  (robust version is Phase 5).

**Tasks**
- [x] Extend `data/rules/email-filter.json`: `immigration_sender_domains`
      (a domain → country_code map, e.g. `imigrasi.go.id` → `ID`, `ica.gov.sg`
      → `SG`, `cbp.dhs.gov`/`esta.cbp.dhs.gov` → `US`, plus TH/PH/IN entries —
      deliberately **disjoint** from `allow_sender_domains`), `immigration_keywords`
      (arrival/entry/disembarkation card, e‑VOA, TDAC, SG arrival card, ESTA
      approved, visa granted…).
- [x] `email_filter.py`: `FilterRules` gained the two new fields (defaulted, so
      the existing `RULES = FilterRules(...)` test fixture didn't need updating);
      `classify_immigration()` (a structural sibling of `classify()`, not a call
      into it) + `immigration_country_for()` set `looks_like_immigration` locally
      — never leaves the box, never unions with the travel allow-list.
- [x] `services/immigration.py`: `find_matching_trip` (date‑overlap +
      `_todo_entry_card`, preferring a same-country trip when the sender's
      domain maps to one — literally imports `_overlaps`/`MATCH_SLACK_DAYS`
      from `review.py`, per the plan), `propose_confirmation` (idempotent per
      email, refuses an unflagged email even if called directly — defence in
      depth), `run_immigration_matching` (the batch entrypoint, no model
      needed), `accept_confirmation` (the only writer — flips `entry_card` to
      `approved`, stamps `source=email`, reuses `review.NotAcceptable`).
- [x] Surface these proposals in the Review queue distinctly from bookings:
      added `Extraction.kind` (`booking`|`immigration`, migration `f086d52834fb`
      with the usual `server_default` trap fixed by hand) so `api/review.py`'s
      `_serialise`/`accept`/`list_review` branch on it; `reject_extraction` is
      reused unchanged (a status flip, kind‑agnostic). Wired
      `run_immigration_matching` into `scheduler.run_poll_cycle` so it runs on
      every ordinary poll, no OpenRouter key needed for this half.
      **Beyond the plan's stated scope:** also gave the frontend a distinct
      `ImmigrationReviewCard` (Review.tsx) instead of leaving `booking` a
      required, always-populated field — a real immigration proposal reaching
      the existing `ReviewCard` (which does `const b = item.booking; b.kind`)
      would otherwise crash the whole Review page the moment production mail
      first matched. `booking`/`immigration` are now both always present on
      the wire, one always null, so a client can destructure either without
      an extra existence check.

**Tests that must pass to proceed**
- [x] A stubbed Indonesian arrival‑card email flags `looks_like_immigration`,
      never `looks_like_travel`, and proposes a confirmation on the ID trip
      (`test_immigration_matching.py`, `test_email_ingest.py`).
- [x] Accept flips `entry_card` → `approved` with the reference; reject writes
      nothing (row‑count assertion, like the booking accept test).
- [x] A non‑immigration email is untouched.
- [x] Bonus, not in the original list but load‑bearing: ambiguous matches
      (two qualifying trips) refuse rather than guess; a trip with no
      outstanding `entry_card` never matches; accept refuses when the
      checklist "moved on" since the proposal was built (card already
      confirmed another way) instead of silently no‑op‑ing; accepting twice
      refuses the second time; `propose_confirmation` is idempotent per email.
- [x] `pytest backend/tests` green (312 passed, was 284); `ruff check` clean.
      Migration rehearsed against a copy of the real local dev DB (upgrade /
      downgrade / re‑upgrade / `alembic check`, all clean) and then applied to
      it for real (it was already on `b119ab960928` from Phase 3's session).
- [x] `npm test` green (45 passed, was 42 — added `Review.test.tsx` covering
      both card kinds rendering, accepting with a typed‑in reference, and
      rejecting) and `tsc --noEmit` clean.
- [x] Browser (local, no passkey, backend on port 8010 with the frontend proxy
      temporarily retargeted and reverted, same as Phase 3): seeded a real
      `todo` `entry_card` requirement + a flagged immigration `EmailMessage`
      on the real local dev DB's Indonesia trip via the actual service
      functions (not raw SQL), confirmed the Review queue rendered the
      "Immigration" pill + "Arrival card confirmation" card with no console
      error, typed a reference, clicked Accept, watched the requirement flip
      to `approved`/`source=email`/the typed reference over the API, and
      confirmed the same row then shows correctly selected ("Approved") in
      the trip's generic Paperwork section (proving decision 5's "reuses the
      existing Requirement table, CRUD, and Paperwork UI" end to end). All
      seeded rows were deleted afterward; the local dev DB carries no lasting
      change beyond the schema migration.

**Notes for the next engineer**
- `Extraction.kind` is the discriminator; `payload_json` for an immigration
  row is intentionally tiny (`{"requirement_kind": "entry_card"}`) — Phase 4
  never reads the email body for a reference or a nationality, that's Phase 5
  once picking the email *is* the consent to send it to the extractor. The
  reference on accept comes from a reviewer‑typed override
  (`AcceptPayload.reference`), not from parsing.
- `propose_confirmation`/`find_matching_trip` only ever target
  `RequirementKind.entry_card` — deliberately narrow, matching the worked
  example. A trip with a `todo` `visa` but an already-`approved` `entry_card`
  will never get an immigration proposal, by design.
- The stray unrelated `python.exe` on port 8000 noted in Phase 3 was still
  there this session; same workaround (backend on 8010, proxy retargeted and
  reverted) was used again.

Commit: `79e91f5`

---

## Phase 5 — Manual immigration extraction (3‑day list) + discrepancy flag

**Why:** decision 4's second half — pick an *unflagged* immigration email from
the recent‑emails list and extract it with the LLM for richer fields (reference,
validity, **nationality/passport** → the loud discrepancy flag).

**Assumptions to validate**
- The existing `extract_email` / `extract_selected` D2 flow can branch to an
  **immigration‑document** tool when the picked email looks immigration‑shaped.

**Gotchas / risks**
- Per‑message consent still governs (picking the email *is* the consent).
- Discrepancy: compare extracted nationality vs the trip's selected passport →
  persist + render **loudly** (decision 3).

**Tasks**
- [x] `IMMIGRATION_DOC_TOOL` (strict, in `extraction.py` alongside the booking
      tools) + `ExtractionModel.extract_immigration_document` (Protocol +
      `OpenRouterModel` impl) + `validate_immigration_document`/
      `ImmigrationDocument`. `services/immigration.py#extract_selected_immigration`
      orchestrates: calls the model, validates, and calls a **generalised**
      `find_matching_trip(session, email, kind=, country_hint=)` — Phase 4's
      version hardcoded `entry_card`; Phase 5 passes whatever kind the
      document read, and a `country_hint` from the model's own reading that
      wins over the sender-domain guess. `_todo_entry_card` was renamed
      `_todo_requirement(session, trip_id, kind)` to match.
- [x] Review UI: `POST /api/review/emails/{id}/extract?kind=immigration`
      (new `kind` query param, default `booking` — the existing endpoint,
      not a new route) branches to `extract_selected_immigration`.
      `RecentEmailsPanel` now shows two buttons per email ("Extract" /
      "As immigration doc") plus a "Gov mail" pill when
      `looks_like_immigration` is set, so a flagged-but-unread email is
      visible before picking either. Accept reads `reference`/`nationality`
      pre-filled from the model's own reading (reviewer's typed override
      still wins) — `accept_confirmation` (Phase 4) now reads
      `requirement_kind`/`reference`/`nationality` off the proposal's own
      `payload_json` instead of assuming `entry_card`.
- [x] Discrepancy detection + loud banner on the trip detail + card. New
      `Requirement.discrepancy_nationality` (migration `50cd760e587a`,
      nullable, no `server_default` trap) stores the raw fact — the
      nationality a Phase 5 reading named — unconditionally on accept.
      **Deliberately not a stored verdict**: `services.trips.trip_readiness`
      compares it against the trip's *currently* selected passport at every
      read, so flipping the passport later to match clears the banner
      without the row ever being touched again (verified live in the
      browser, see below). `discrepancy` is now a field on both
      `ReadinessSummary` (compact, on trip cards) and the full `Readiness`
      (trip detail) — checked independent of whether a policy is even
      cached, since an accepted confirmation is a real fact regardless of
      readiness state. On the trip detail panel this is the one thing that
      still renders on a **past** trip (`ReadinessSection` grew a `quiet`
      prop for this — Phase 3's "gate the whole section on status" became
      "gate everything except a loud discrepancy on status").

**Tests that must pass to proceed**
- [x] Fake model extracts an arrival card w/ MX nationality on a US‑selected trip
      → discrepancy recorded; accept still confirms the card but the flag shows
      (`test_discrepancy_shows_after_accepting_a_mismatched_nationality` at the
      service level, `test_accepting_an_immigration_document_with_a_nationality_mismatch_flags_discrepancy`
      end-to-end through the API).
- [x] `npm test` green (53 passed, was 45) + browser verify the loud flag
      renders — see below. No pixel screenshot (Browser pane wasn't
      displayed for compositing this session either), but the full
      `get_page_text` transcript is the evidence trail, same as Phases 3–4.
- [x] Bonus, not in the original list: the country-hint-overrides-sender-domain
      case; targeting a kind other than `entry_card` (Phase 4's local matcher
      never can); a reading naming neither a kind nor a nationality is
      rejected as unusable; the discrepancy clears once the passport is
      flipped to match (both at the service level and live in the browser).
- [x] `pytest backend/tests` green (331 passed, was 312); `ruff check` clean.
      Migration rehearsed against a copy of the real local dev DB, then
      applied to it for real.

**Tests that must pass to proceed, browser verification detail**
Seeded a real `todo` `entry_card` requirement + a flagged immigration email on
the local dev DB's Indonesia trip (MX passport selected), then ran the actual
`extract_selected_immigration`/`accept_confirmation` functions with a fake
model reading a **US** nationality — a genuine mismatch against the trip's
selected MX passport. Confirmed: the card showed "⚠️ The arrival card
confirmation names a US passport, but this trip has MX selected..."; the trip
detail panel showed a red "Passport mismatch" banner with the same sentence,
above the (now-quiet, since no policy is cached) "Not checked yet" line; the
`Requirement` correctly showed "Approved" in the generic Paperwork fallback.
Flipping the passport selector to US live-cleared the banner on both the card
and the detail panel, no reload needed — proving the "live comparison, not a
stored verdict" design end to end. Flipped back to MX and deleted all seeded
rows afterward; the local dev DB carries no lasting change beyond the schema
migration.

**Notes for the next engineer**
- `services/immigration.py` now does double duty: Phase 4's fully-automatic,
  LLM-free `propose_confirmation`/`run_immigration_matching` (always
  `entry_card`, matched by sender-domain + date only) and Phase 5's manual,
  model-read `extract_selected_immigration` (any kind, model's own country
  read, nationality). Both funnel through the same `accept_confirmation` —
  it reads everything it needs (`requirement_kind`, `reference`,
  `nationality`) from the proposal's own `payload_json`, so the accept
  endpoint and the UI never need to know which path produced a given
  immigration proposal.
- `RequirementKind` values other than `custom` line up exactly between
  `services.trips.POLICY_REQUIREMENT_KINDS` (Phase 2) and
  `extraction.IMMIGRATION_REQUIREMENT_KINDS` (Phase 5) — both are "every
  kind except custom". If a future kind is ever added that shouldn't be
  policy-driven or document-confirmable, that assumption needs revisiting in
  both places.
- The stray unrelated `python.exe` on port 8000 (noted in Phases 3 and 4) was
  *not* checked this session — same 8010 + temporarily-retargeted-proxy
  workaround was used without re-verifying it's still there. Worth an actual
  look before Phase 6's deploy, since it's unrelated to this feature and its
  origin is still unknown.

Commit: `9f1b94e`

---

## Phase 6 — Deploy, verify on prod, document

**Why:** README §4 — do not skip the deploy; verify the shipped bundle.

**Assumptions to validate**
- The migration chain (`... → 50cd760e587a`) runs cleanly on container start,
  against the *real* prod DB — not just a local copy. `deploy/entrypoint.sh`
  runs `alembic upgrade head` before the app starts; `deploy.sh` snapshots the
  DB first (`sqlite3 .backup`), so a bad migration is recoverable.
- `YAYO_OPENROUTER_API_KEY` is already set in prod's `deploy/.env` (Gmail
  ingest has been live, §5), so `get_policy` will actually fetch — the feature
  is not silently degraded to `unknown` on the box.

**Gotchas / risks**
- **Do not skip the deploy** (README §4's loud warning — three commits once
  shipped late). After the user pushes, deploy immediately, then grep the
  shipped JS bundle for a new string to prove it's really live.
- The stray unrelated `python.exe` on local port 8000 (Phases 3–5) is a
  *local dev* nuisance only — it has nothing to do with prod and does not
  affect the deploy.
- Four new migrations land on prod at once (`b119ab960928`, `f086d52834fb`,
  `50cd760e587a`, plus Phase 0's already-counted one) — all additive
  (new table + nullable/defaulted columns), all rehearsed locally against a
  copy of the real DB across the phases.

**Tasks**
- [x] Update `README.md`: §1 gained an "Immigration readiness" subsection; §2
      updated the tables list (15→17), migration count (5→9), the file tree
      (`entry_policy.py`, `immigration.py`, `lib/immigration.ts`), the feature
      table, and the static-reference-data table (the never-built
      `entry-requirements.json`/`visa-free.json` entries removed, immigration
      allow-list documented); §5 gained the `looks_like_immigration` filter
      note and a full "immigration path" subsection; §7 dropped the stale
      "`Requirement` rows … nothing creates them" and "visa-free dataset"
      items and refreshed the frontend test count (28→53).
- [x] **User pushed**; local and `origin/main` in sync.
- [x] Assistant ran `deploy/deploy.sh` over SSH — image rebuilt, container
      recreated, **healthy after 4s**. `alembic upgrade head` on the box took
      prod to `50cd760e587a (head)`, applying all four new migrations against
      the real DB (3 existing trips preserved). Verified the shipped bundle
      (`/assets/index-DoJbx2z4.js` — same hash as the local `npm run build`)
      contains `Passport mismatch`, `Immigration readiness`, and `As
      immigration doc`. `/api/health` returns ok, `email_ingest_enabled=true`.
      Read-only schema check on the live DB confirmed the `entry_policy`
      table + `requirement.source` / `requirement.discrepancy_nationality` /
      `email_message.looks_like_immigration` / `extraction.kind` columns all
      present; 0 policies cached yet (expected — none fetched until a trip is
      opened on the live site).
- [x] Stamp every phase's commit hash above (Phases 0–5 all stamped;
      Phase 6's docs commit is `8911d5c`).

**Verification once deployed** — all passed, see the deploy task above. The
one thing left to happen organically: opening a real upcoming trip on the live
site will fetch and cache its first real policy (prod has the OpenRouter key),
turning that trip's badge from `❔ unknown` into a real reading.

**Notes for the next engineer**
- The frontend `dist/` is gitignored and built inside the Docker image, so
  there is nothing to commit from `npm run build` — the bundle-string check is
  purely a post-deploy verification, not a build step.
- Prod already had the Phase 0 migration (`b119ab960928`) applied? No — check
  `alembic current` on the box before assuming. Locally the dev DB was found on
  `b119ab960928` at the start of Phase 4 (Phase 3's session had applied it),
  but **prod's state is independent**; the entrypoint's `alembic upgrade head`
  will apply whatever chain prod is missing, in order.

Commit (docs): `8911d5c`. Deploy: shipped and verified on prod
(`travel.foryayo.com`), DB at `50cd760e587a (head)`, healthy after 4s.

---

## Decisions locked with the user (2026‑08‑04, round 2)

6. **Confirmation flow: Review‑accept, no auto‑confirm.** Kept as designed —
   an immigration email proposes a confirmation, it only lands on accept.
7. **No refresh mechanism at all.** Not just "no button" — Phase 1 does not
   build `POST /api/policy/refresh` or any re‑fetch path. A policy is queried
   once per `(country_code, nationality)` and cached forever; staleness risk is
   accepted explicitly. (If a policy ever needs correcting, that's a manual DB
   edit or a future deliberate migration, not a feature.)
8. **Vaccination / insurance (and onward‑ticket) are asked‑for but
   display‑suppressed when not required.** The policy call still asks the model
   about all `RequirementKind`s so nothing is missed, but `sync_requirements`
   only ever materializes a row for a kind the policy actually flags as
   required — so "not required" kinds simply produce no row and render nothing.
   This was already the natural behavior of "materialize only what's needed";
   stated explicitly here so Phase 2 doesn't accidentally render placeholder
   "not required" rows for these three kinds. Visa and entry_card remain the
   headline kinds (always shown when required, per the Indonesia example).

---

## Decisions locked with the user (2026‑08‑05, round 3)

Follow‑up: the **Arrival Card** and **Onward Ticket** sections should stop being
hand‑set dropdowns and instead report, automatically, what the mailbox and the
booked journeys already know. Four decisions were locked before planning:

9. **Arrival card = a 3‑state automated indicator, and the accept boundary
   holds.** No dropdown. It reads one of three states, driven by the immigration
   email pipeline (Phases 4–5), never by a manual status pick:
   - **`none`** — no arrival/entry‑card confirmation email has matched this trip.
     Renders *"No arrival card confirmation received."*
   - **`received`** — a confirmation email matched this trip and is **waiting in
     the Review queue** (a pending `Extraction` of kind `immigration` for
     `entry_card`, `suggested_trip_id == this trip`). Renders *"Confirmation
     email received — confirm it in Review."* This surfaces receipt the moment
     mail lands **without** writing trip data: the confirmation still only
     *counts* once a human accepts it (the accept boundary from README §5 /
     design‑principles, unchanged).
   - **`confirmed`** — the `entry_card` requirement is `approved` (an immigration
     email was accepted, Phase 4/5). Renders *"Arrival card confirmed"* + the
     reference.
10. **Onward ticket = derived live from booked journeys, not a stored status.**
    No dropdown, no new email classifier, no new allow‑list. An onward ticket is
    *confirmed* when a booked journey (`Leg`) departs the trip's country **around
    the trip's end date** — which is exactly how a real onward/return flight is
    already recorded in this app: every `Leg` is an *arrival into* a country, so
    the journey that carries you **out** of trip X's country is the inbound
    `Leg` of a later trip, whose `depart_at` sits near trip X's `end_date`. These
    legs already come from journey‑confirmation emails (booking extraction) or
    manual entry, so "a journey confirmation email that aligns with the end of
    the trip" is a `Leg` that departs a different country than trip X's, dated
    within a few days of trip X's end. Computed at read time (like the
    discrepancy flag) — nothing is written, so a later booking flips it to
    confirmed on the next read without touching any row.
11. **Both indicators show only when the LLM entry policy requires them** —
    arrival card only when `policy.entry_card_required`, onward ticket only when
    `policy.onward_ticket_required`. Unchanged from decision 8's gating: a
    country that doesn't need an arrival card or onward proof shows neither line.
12. **Fully automated — no manual override, no escape hatch.** Removing the
    dropdown removes the ability to hand‑mark "I filed a paper card" or "I booked
    onward travel offline." If no matching email/journey exists, the indicator
    reads *not confirmed*, full stop. (A paper‑card traveller can still record
    the journey as a `Leg`, which is the onward‑ticket signal; the arrival card
    simply relies on the email arriving.)

**What this supersedes:** the `entry_card` and `onward_ticket` rows stop being
user‑editable via the readiness `<select>`. `visa` / `eta` / `insurance` /
`vaccination` keep their dropdowns (nothing automated confirms them yet). The
`entry_card` **Requirement row** is still the store of record for
*confirmed* (its `approved` status, flipped only on accept); the
`onward_ticket` Requirement row becomes vestigial for *status* — it records only
that onward proof is **required**, while *confirmed* is derived from `Leg`s live.

---

## Phase 7 — Backend: automate the arrival‑card & onward‑ticket readings

**Why:** the two indicators are renderings of state the backend already holds —
the immigration‑email pipeline for the arrival card, the `Leg` table for the
onward ticket. Compute both in `trip_readiness` so the frontend (Phase 8) is a
pure rendering change and the trip‑list badge and detail panel read identical
state (the same rule that put `readinessBadge` in one shared module in Phase 3).

**Assumptions to validate**
- A pending immigration `Extraction` for `entry_card` on a trip is queryable by
  `(kind=immigration, status=pending, suggested_trip_id=trip.id)` and its
  `payload_json.requirement_kind == "entry_card"` — matches how Phase 4 writes it.
- Every `Leg` records the country it *arrived into* (`Leg.country_code`) and a
  `depart_at`; there is **no** airport→country lookup, so "departs the trip's
  country" is approximated as "arrives a **different** country, dated near this
  trip's end." Validated against `models.py` (`Leg.country_code` is the arrival
  country; return travel isn't tracked).

**Gotchas / risks**
- **The accept boundary still holds** (README §5). The `received` state is read
  from a *pending* proposal — `trip_readiness` must not accept, write, or flip
  anything. It only *reads* that a proposal is waiting. Confirmation stays a
  human accept in Review.
- **`ready` must respect the derived states, not the stored `entry_card` /
  `onward_ticket` status.** `onward_ticket`'s stored status stays `todo` forever
  now (nothing flips it), so the old `all(status in _SETTLED)` would read a
  confirmed‑by‑journey trip as `action`. Compute *settled* per kind:
  `entry_card` settled ⇔ `arrival_card.state == "confirmed"`; `onward_ticket`
  settled ⇔ `onward_ticket.confirmed`; every other kind settled ⇔ status in
  `_SETTLED_STATUSES` (unchanged).
- **Empty / undated legs.** A `Leg` with no `depart_at`, or `country_code == ""`,
  can't confirm onward travel — skip it (don't let an empty arrival country
  `"" != "ID"` sneak through as "a different country").
- **Window pick.** "Aligns with the end of the trip" = `abs(depart_date −
  end_date) ≤ ONWARD_SLACK_DAYS`. Use a small symmetric window (`ONWARD_SLACK_DAYS
  = 3`) — forgiving enough for a same‑night red‑eye out or a next‑morning flight,
  tight enough not to grab an unrelated trip two weeks later. Its own named
  constant in `trips.py` (don't couple readiness to `review.MATCH_SLACK_DAYS`,
  which governs a different thing).
- **Compact badge parity.** `list_trips` filters `trip_readiness` down to a
  compact key set (`api/trips.py`); add `onward_ticket` to that set so the card
  badge can say "onward ticket not confirmed" the way it already can for the
  arrival card. `arrival_card` is already in the set.

**Tasks**
- [x] `services/trips.py` — replaced the `arrival_card` reading with the 3‑state
      shape `{name, state: "none"|"received"|"confirmed", reference}` (dropped
      the old `confirmed`/`status` keys). Added `_arrival_card_reading` and
      `_pending_entry_card_email`.
- [x] `services/trips.py` — added `_onward_ticket_reading(session, trip, code,
      policy)` returning `{required, confirmed, journey}` when
      `policy.onward_ticket_required`, else `None`; `ONWARD_SLACK_DAYS = 3`;
      scans every dated `Leg` with a truthy `country_code != code` within the
      window, earliest first.
- [x] `services/trips.py` — `trip_readiness` computes both readings, returns
      `onward_ticket`, and uses a per‑kind `_settled` rule (entry_card ⇔
      `arrival_card` confirmed; onward_ticket ⇔ journey found; else stored
      status). `_empty_readiness` includes `onward_ticket: None`.
- [x] `api/trips.py` — `"onward_ticket"` added to the compact readiness key set.

**Tests that must pass to proceed**
- [x] Arrival card **none / received / confirmed** in one test, with a row‑count
      assertion proving `received` writes no `Requirement` (accept boundary).
- [x] A pending proposal for a *different* kind (visa) does **not** mark the
      arrival card `received`.
- [x] Onward ticket **not required** → `readiness["onward_ticket"] is None`.
- [x] Onward ticket **required + confirmed** by a later trip's inbound `Leg`
      (SG, departing the day the TH stay ends) → `confirmed`, journey populated,
      trip flips to `ready`.
- [x] Onward ticket **ignores** a same‑country leg and an out‑of‑window leg.
- [x] Updated the two existing assertions (`arrival_card["confirmed"]` → `state`,
      and the `na` full‑dict shape gained `onward_ticket: None`); `list_trips`
      compact test asserts `onward_ticket` rides along.
- [x] `pytest backend/tests` green (336 passed, was 331); `ruff check app tests`
      clean.

**Notes for the next engineer**
- `_onward_ticket_reading` approximates "departs this trip's country" as
  "arrives a **different** real country, dated within `ONWARD_SLACK_DAYS` of the
  trip's end" — there is no airport→country lookup, and `Leg.country_code` is
  the *arrival* country (return travel isn't tracked). This is a proxy: a leg
  from an unrelated trip that happens to depart a different country near this
  end date would count. Acceptable — it still evidences booked onward travel
  around that time — but worth knowing if a false positive ever surfaces.
- The `onward_ticket` **Requirement row** (still materialised by
  `sync_requirements` when required) is now vestigial for *status*: nothing
  flips it, and `_settled` reads the derived journey, not the row. It's kept
  only so the checklist knows onward proof is required. If you ever want it to
  reflect confirmation in the DB, that'd be a *write* on a derived signal —
  deliberately avoided here, same reasoning as the live discrepancy comparison.
- `arrival_card`'s `received` state reads a *pending* `Extraction`; it never
  writes. Confirmation still requires a human accept in Review (the row flips to
  `approved`, which is what `confirmed` reads). Don't be tempted to auto‑flip on
  a pending proposal — that would breach the accept boundary (decision 9).

Commit: `7ccafe9`

---

## Phase 8 — Frontend: automated indicators replace the two dropdowns

**Why:** the visible half of the request — "no drop down," read‑only automated
lines for the arrival card and onward ticket.

**Assumptions to validate**
- The readiness `<select>` for `entry_card`/`onward_ticket` is the only place
  those two kinds are user‑editable (the generic Paperwork section already
  filters out system‑sourced immigration rows, Phase 3) — so removing the two
  selects removes the last edit path, matching decision 12.

**Gotchas / risks**
- Keep `visa`/`eta`/`insurance`/`vaccination` on their dropdowns — only
  `entry_card` and `onward_ticket` become read‑only.
- Past‑trip `quiet` gating and the `na`/`unknown` early returns are unchanged.
- The compact badge (`lib/immigration.ts`) reads `arrival_card.confirmed` today;
  switch it to `arrival_card.state === "confirmed"` and add an onward note when
  `onward_ticket?.required && !onward_ticket.confirmed`.

**Tasks**
- [x] `types.ts` — `ArrivalCardReading` → `{name, state, reference}` (+
      `ArrivalCardState`); added `OnwardTicketReading` and `onward_ticket` on
      `ReadinessSummary` (inherited by `Readiness`).
- [x] `lib/immigration.ts` — `readinessBadge`: arrival‑card note keys off
      `state !== "confirmed"`; added the onward‑ticket note. One shared source.
- [x] `TripDetail.tsx` — `entry_card` and `onward_ticket` render via new
      read‑only `ArrivalCardRow` / `OnwardTicketRow` components; every other kind
      keeps its `<select>`. Added `.readiness-status` CSS (reuses the badge
      palette). No status handler is invoked for the two automated kinds.
- [x] `lib/immigration.test.ts` — arrival‑card `state` mapping (none / received /
      confirmed) + onward note (unconfirmed / confirmed); fixtures updated.
- [x] Added `onward_ticket: null` to the `arrival_card: null` readiness fixtures
      in `Trips.test.tsx`, `TripDetail.test.tsx`, `Calendar.test.tsx`.

**Tests that must pass to proceed**
- [x] `npm test` green (56 passed, was 53); `tsc --noEmit` clean; `vite build`
      clean.
- [x] Browser (local, backend‑local on 8010, proxy temporarily retargeted and
      reverted — same pattern as Phases 3–5; the stray port‑8000 process was left
      untouched). Seeded trip 3 (Batam/Indonesia, MX passport) with a cached
      policy requiring **both** arrival card and onward ticket, via the real
      `sync_requirements`. Verified, live, all through the readiness section with
      **zero `<select>`s** in it:
      - base → *Arrival card: "⚠️ No confirmation email received"*, *Onward
        ticket: "⚠️ No onward ticket confirmed"*; card badge *"⚠️ arrival card
        not yet confirmed · onward ticket not confirmed"*.
      - + a pending immigration proposal → *Arrival card: "📥 Confirmation email
        received — confirm it in Review"* (and **no** Requirement written — the
        accept boundary held).
      - + an onward `Leg` (SG, departing the trip's end date) → *Onward ticket:
        "ZZ 7 → Singapore (Aug 24, 2026) · ✅ Onward ticket confirmed"*.
      - entry_card approved → *Arrival card: "✅ Confirmed by email · e‑CD
        88213"*; card badge flips to *"✅ Ready"*.
      No console errors throughout. All seeded rows removed afterward (trip 3
      back to `unknown`, no residue); no pixel screenshot (Browser pane wasn't
      compositing this session, same as Phases 3–5 — the JS/`get_page_text`
      transcript is the evidence trail).

**Notes for the next engineer**
- The readiness *summary* line reads "Nothing required to enter" whenever
  `permit` is null (`permitSummary` returns null). During verification the
  seeded policy had `permit_type = None`, so that line showed even though the
  checklist had two required items — a pre‑existing copy quirk of the summary
  header, unrelated to this phase (a real fetched policy carries a permit type).
  Left as‑is; flag it if the header ever needs to reflect "action" more honestly.

---

## Phase 9 — Deploy, verify on prod, document

**Why:** README §4 — don't skip the deploy; verify the shipped bundle.

**Assumptions to validate**
- **No migration this time** — Phases 7–8 add no columns or tables (the reading
  is derived; `discrepancy_nationality`‑style live comparison). So the deploy is
  a plain image rebuild, no `alembic upgrade` risk. Confirm `alembic check`
  reports no drift before shipping.

**Gotchas / risks**
- **Do not skip the deploy** (README §4). After the user pushes, deploy, then
  grep the shipped JS bundle for a new string (e.g. "No arrival card
  confirmation") to prove it's live.

**Tasks**
- [ ] Update `README.md` (§1 immigration‑readiness subsection: note the arrival
      card and onward ticket are now automated/read‑only; §5 if the wording
      references the dropdowns; frontend test count).
- [ ] User pushes; assistant deploys via `deploy/deploy.sh` over SSH and verifies
      the bundle string + `/api/health`.
- [ ] Stamp Phases 7–9 commit hashes.

**Notes for the next engineer**
- *(filled in on completion)*
