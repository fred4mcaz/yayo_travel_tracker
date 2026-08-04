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
- [ ] `services/entry_policy.py`: `readiness_passport(entry) -> "US"|"MX"`.
- [ ] `services/trips.py`: `sync_requirements(session, trip)` (materialize/
      reconcile `system` rows from the policy) and `trip_readiness(session, trip)`
      → `{ state: ready|action|unknown|na, passport, is_default_us, permit,
      permitted_days, checklist:[{kind,label,status}], arrival_card, advisory,
      checked_on, alternate_passport_hint }`.
- [ ] Wire `sync_requirements` into the trip mutation + passport‑change paths.
- [ ] Add `readiness` (compact) to `list_trips` payload and (full) to
      `trip_detail` payload.

**Tests that must pass to proceed**
- [ ] US → Japan: visa‑free, **no** `visa` row; readiness `ready` pre‑arrival‑card.
- [ ] US → Indonesia: `visa` (VoA) + `entry_card` rows, state `action`.
- [ ] Passport US→MX flip reconciles rows; a user‑advanced status survives.
- [ ] Undated/no‑country trip → readiness `na`, no rows.

Commit: `_____`

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
- [ ] `types.ts`: `Readiness` + add to `TripSummary`/`TripDetail`; `source` on
      `Requirement`.
- [ ] Trip card (`Trips.tsx`): compact readiness badge (✅ ready / ⚠️ action /
      ❔ unknown) with the permit one‑liner.
- [ ] `TripDetail.tsx`: "Immigration readiness" section — permit summary, the
      US‑default note / MX‑selected, **alternate‑passport hint** ("US would be
      visa‑free"), the checklist with the existing status `<select>`, arrival‑card
      confirmed indicator, the **loud discrepancy** slot (wired in Phase 5), and
      the advisory + "checked <date>" line (no Refresh button).
- [ ] vitest for the badge state mapping.

**Tests that must pass to proceed**
- [ ] `npm test` green incl. new badge test.
- [ ] Browser (local, no passkey): open an Indonesia trip → VoA + arrival‑card
      shown; flip passport → section recomputes; past trip → silent. Screenshot.

Commit: `_____`

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
- [ ] Extend `data/rules/email-filter.json`: `immigration_sender_domains`,
      `immigration_keywords` (arrival/entry/disembarkation card, e‑VOA, TDAC, SG
      arrival card, ESTA approved, visa granted…).
- [ ] `email_filter.py`: set `looks_like_immigration` locally (never leaves box).
- [ ] `services/immigration.py`: match a flagged email → trip, build an
      immigration **confirmation proposal**; accept path flips the requirement.
- [ ] Surface these proposals in the Review queue distinctly from bookings.

**Tests that must pass to proceed**
- [ ] A stubbed Indonesian arrival‑card email flags `looks_like_immigration`,
      never `looks_like_travel`, and proposes a confirmation on the ID trip.
- [ ] Accept flips `entry_card` → `approved` with the reference; reject writes
      nothing (row‑count assertion, like the booking accept test).
- [ ] A non‑immigration email is untouched.

Commit: `_____`

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
- [ ] `IMMIGRATION_DOC_TOOL` (strict) + `extract_selected_immigration`.
- [ ] Review UI: allow choosing "extract as immigration document" for a recent
      email; accept flips the requirement and records nationality/reference.
- [ ] Discrepancy detection + loud banner on the trip detail + card.

**Tests that must pass to proceed**
- [ ] Fake model extracts an arrival card w/ MX nationality on a US‑selected trip
      → discrepancy recorded; accept still confirms the card but the flag shows.
- [ ] `npm test` + browser verify the loud flag renders. Screenshot.

Commit: `_____`

---

## Phase 6 — Deploy, verify on prod, document

**Why:** README §4 — do not skip the deploy; verify the shipped bundle.

**Tasks**
- [ ] User pushes; run `deploy/deploy.sh` over SSH; verify the new strings are in
      the shipped bundle.
- [ ] Update `README.md` (§2 rules files, §5 immigration path, §7 remove
      "Requirement rows … nothing creates them").
- [ ] Stamp every phase's commit hash above.

Commit: `_____`

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
