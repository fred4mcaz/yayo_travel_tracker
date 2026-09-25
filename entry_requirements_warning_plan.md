# Entry-requirements warning plan

**Why this plan exists.** On a September 2026 trip to London, the traveller was
almost denied boarding because he had no UK ETA. The site is *supposed* to
prevent exactly this. It did not — and the painful part is that **the data was
correct all along**. This plan makes the warning impossible to miss, names the
actual documents required, and stops the model from mislabelling
ETA-only countries as needing a visa.

---

## The diagnosis (read this first — it shapes every phase)

Verified against the live production database on 2026-09-25:

- The London trip is **trip 14, 2026-09-21 → 2026-09-30** (ongoing today).
- The entry policy for **GB / US** was fetched on 2026-08-26 and *correctly*
  recorded **`eta_required = 1`**.
- The **ETA requirement row already exists** (`requirement` id 10, `kind=eta`,
  `status=todo`, `source=system`).
- Therefore `trip_readiness` was returning **`state = "action"`** the whole time.

So this was **not** a "confidently wrong LLM" failure. The system knew. The
failure was in *surfacing*:

1. **The warning is passive.** `action` state renders only as (a) a small chip
   on the trip card in the Trips tab and (b) a section partway down the trip
   detail. The app uses loud banners for missing hotels / missing flights, but
   immigration `action` gets none. `App.tsx` has no readiness surface at all.
2. **The one visible chip never said "ETA".** `readinessBadge` builds its text
   from the permit summary (+ arrival-card / onward notes), *not* the checklist.
   For London it read **"⚠️ E-visa required"** — the ETA is invisible unless you
   open the trip and read the checklist.
3. **No urgency, plus a silent "unknown" hole.** A requirement 200 days out
   looks identical to one 2 days out; and because `trip_readiness` only ever
   *reads* the cache, a trip can sit at `unknown` ("Not checked yet") — silent —
   until some later edit triggers a fetch.
4. **Secondary data-quality bug.** The model labelled the UK as
   `permit_type=evisa` + `visa_required=true`. Reality for a US passport is
   *visa-free + ETA*. It got `eta_required` right (so this did not cause the
   miss), but it created a spurious "Visa" requirement row and muddled the
   headline. There is **no GB override** in
   `data/rules/entry-policy-overrides.json` (only Indonesia is covered).

### The three approved decisions (locked with the traveller 2026-09-25)

- **Surface:** louder **card badges** that name the documents. No global banner.
- **Unknown hole:** an imminent dated trip with no policy reading becomes its own
  loud "verify manually" warning. **No new LLM calls.**
- **LLM:** move to a **documents-first** reading — the model lists which
  documents/authorizations are required; `permit_type` is demoted to a
  descriptive detail and no longer drives the headline or a visa requirement.
- **No manual dropdowns (added 2026-09-25).** Readiness is purely informational.
  It answers one question: *do I need to do something to enter, or not?* If yes,
  it names the documents/authorizations required. If no, it shows the default
  limits ("Visa-free · 90 days in Japan"). There are **no status pickers** for
  the traveller to fill in. This supersedes README §1's "the other requirement
  kinds keep their status dropdowns" — those dropdowns are removed in Phase 4.
  Automatic confirmations (arrival card via email, onward ticket via a booked
  leg) still clear their own items; everything else simply shows as "required"
  until auto-confirmed. Over-warning is the safe direction and is acceptable.

### The bar for "done"

The London card, loaded against production data, must scream that an **ETA** is
required (naming it), not "E-visa required". A visa-free trip with nothing owed
must stay quiet. An imminent trip whose rules were never checked must warn.

---

## Conventions for whoever executes this

- **Windows PowerShell 5.1 dev box; Linux server.** One command per fenced
  block. Never chain with `&&` / `;`. See the root `CLAUDE.md`.
- **Verify in a real browser, do not trust the code** (README §3). Local dev
  needs no passkey and pulls a throwaway copy of the prod DB on every start, so
  the London trip is right there to test against.
- **A task is complete only when committed.** Record the commit hash next to the
  checkbox. End every commit message with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **The accept boundary and the no-refresh rule still hold** (README §1, §5).
  This plan does not touch them.
- Run after each phase, from repo root:
  - `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`
  - from `frontend/`: `npm test`, then `npx tsc --noEmit` (or `npm run build`).

---

## Phase 1 — Documents-first entry-policy call (backend, LLM)

**Objective.** Reshape the `assess_entry_policy` tool and prompt so the model's
job is "list the documents/authorizations required to enter as a tourist," and
so an ETA/ESTA/eTA/ETIAS is never treated as a visa. `permit_type` stays in the
schema but becomes purely descriptive; `visa_required` is decided on its own
merits, not forced by `permit_type`.

**Narrative — why.** The UK reading (`evisa` + `visa_required=true` +
`eta_required=true`) is the schema letting `permit_type` and the booleans
contradict each other. The requirement checklist is *already* generated from the
`{kind}_required` booleans (`POLICY_REQUIREMENT_KINDS` in `services/trips.py`),
so the booleans are the real source of truth. This phase makes the prompt match
that reality and stops the spurious visa row.

**No database migration.** The columns already exist and are the document
booleans we want; we are changing *semantics and prompt*, not the table. Do not
drop `permit_type` — `permitted_days` and the descriptive summary still use it,
and dropping a column risks the SQLite `NOT NULL` trap (README §6).

**Assumptions to validate first (tick before coding):**
- [ ] `ENTRY_POLICY_TOOL` in `backend/app/services/entry_policy.py` is the only
      place the tool schema/prompt lives.
- [ ] `POLICY_REQUIREMENT_KINDS` (in `services/trips.py`) drives requirement rows
      purely off `getattr(policy, f"{kind.value}_required")` — confirm `visa`,
      `eta`, `entry_card`, `insurance`, `vaccination`, `onward_ticket` all map to
      a `*_required` bool on `EntryPolicy`.
- [ ] `validate_policy` is the single validation gate and is reused by the
      override loader (`_override_row`).

**Gotchas / risks:**
- Strict function-calling means the schema's `required` array and enum must stay
  self-consistent, or the provider rejects the call. Keep `permit_type` nullable.
- `validate_policy` must keep coercing defensively (README: "trust nothing from
  the model"). Do not remove the shape checks.
- Existing `test_entry_policy.py` fakes return fixed dicts — they must still
  validate under the revised schema.

**Tasks:**
- [ ] Rewrite the tool `description` and each property `description` so the model
      is told: the required documents are the headline; `visa_required` is false
      for a visa-free entry even when an ETA is needed; an ETA/ESTA/ETIAS is an
      Electronic Travel Authorization, **not** a visa and **not** an e-visa;
      `permit_type` describes *how* you enter and is `visa_free` when only an ETA
      (or nothing) is needed.
- [ ] Adjust the user-message prompt in `OpenRouterPolicyModel.assess_entry_policy`
      to ask "which documents / authorizations are required to enter …" and to
      spell out the ETA-is-not-a-visa rule.
- [ ] In `validate_policy`, add a light normalisation guard: if `permit_type` is
      `visa_free`/`residency`/`citizen`, force `visa_required=False` (they are
      mutually exclusive by definition). Do **not** infer the reverse. Document
      why with a comment.
- [ ] Update `backend/tests/test_entry_policy.py` with a UK-shaped case:
      `permit_type=visa_free`, `visa_required=false`, `eta_required=true` →
      validates, and (via `sync_requirements`) produces an `eta` row and **no**
      `visa` row.

**Phase gate:** `pytest backend/tests -q` green. Commit. Record hash: `______`.

---

## Phase 2 — Curated GB override (data, corrects the live trip)

**Objective.** Add human-verified GB entries for **US and MX** to
`data/rules/entry-policy-overrides.json`, so the UK reads visa-free + ETA
authoritatively — beating both the model and the stale cached `evisa` row.

**Narrative — why.** The override layer is the correction path (README §1). An
override beats the cached row (`cached_policy` checks the file before the DB), so
adding GB here **immediately fixes the current London trip** (trip 14) on the
next `sync_requirements` / readiness read: the spurious `visa` row is retired
(it is `source=system`, `status=todo`, so `sync_requirements` may delete it) and
the reading becomes clean visa-free + ETA.

**Assumptions to validate first:**
- [ ] Confirm the *current* UK ETA rules for a **US** passport and a **MX**
      passport with a quick web check (the override discipline requires human
      verification, not model recall). Expected at time of writing: both are
      non-visa nationals, visa-free visitor up to 6 months, **ETA required**.
      Record the source URL and date in the entry's `source`/`checked_on`.
- [ ] `entry-policy-overrides.json` keys and field names exactly mirror the
      `assess_entry_policy` tool (see the existing Indonesia entries).

**Gotchas / risks:**
- `load_overrides` is `@lru_cache`d and the container reads the committed file at
  runtime — this only takes effect **after a deploy that rebuilds the image**
  (memory: data/ changes need `deploy.sh`, not just a git pull).
- A malformed entry is silently skipped (logged). After editing, run the loader
  in a test to confirm both GB rows parse.
- `data/rules/` must keep at least one tracked file — do not delete `.gitkeep`.

**Tasks:**
- [ ] Web-verify UK ETA rules for US and MX; capture source + date.
- [ ] Add `GB`/`US` and `GB`/`MX` entries: `permit_type=visa_free`,
      `visa_required=false`, `eta_required=true`, `permitted_days=180`,
      `entry_card_required=false`, a clear `summary`, an `advisory`, and a
      `source` noting it corrects the `claude-sonnet-5` evisa misread.
- [ ] Add a test (extend `test_entry_policy_overrides.py`) asserting the GB/US
      override loads and that `cached_policy` returns it over any DB row.

**Phase gate:** `pytest backend/tests -q` green; both GB rows load. Commit.
Record hash: `______`.

---

## Phase 3 — Readiness names the outstanding documents (backend payload)

**Objective.** `trip_readiness` (and the compact summary on `GET /api/trips`)
gains an explicit **`outstanding`** list — the required documents not yet
settled, each as `{kind, label}` — computed with the *real* settled logic
(including the derived arrival-card / onward-ticket states, not raw status).

**Narrative — why.** The card badge cannot name "ETA" today because the compact
`ReadinessSummary` carries no checklist (see `frontend/src/types.ts`). Rather
than replicate the tricky `_settled` logic in TypeScript (entry_card and
onward_ticket are "done" by derived reading, not stored status), compute the
outstanding set once in the backend and ship it. Single source of truth.

**Assumptions to validate first:**
- [ ] Find where the **compact** `ReadinessSummary` is assembled for
      `GET /api/trips` (README §2 says compact on the list, full on detail).
      Confirm whether it is a trimmed view of `trip_readiness`'s dict or a
      separate builder — `outstanding` must appear in **both** the compact and
      full payloads.
- [ ] `_settled(item)` inside `trip_readiness` already encodes the correct
      per-kind "done" rule — reuse it; do not re-derive.

**Gotchas / risks:**
- Do not change what `state` means (`na`/`unknown`/`action`/`ready`). Only add
  `outstanding`; existing consumers must keep working.
- `outstanding` must be empty when `state` is `ready` and must be omitted/empty
  (not misleading) when `state` is `unknown` (we don't know the documents) — the
  frontend handles `unknown` separately in Phase 4.
- Keep `outstanding` ordered stably (follow `POLICY_REQUIREMENT_KINDS` order) so
  the badge text is deterministic and testable.

**Tasks:**
- [ ] In `trip_readiness`, build `outstanding = [{kind,label} for each checklist
      item where not _settled(item)]` and add it to the returned dict.
- [ ] Ensure the compact summary builder includes `outstanding` too.
- [ ] Add `outstanding: ReadinessChecklistItem[]` to `ReadinessSummary` in
      `frontend/src/types.ts` (inherited by `Readiness`).
- [ ] Backend tests: for a UK-shaped trip (visa-free + ETA, ETA todo),
      `outstanding` contains exactly `eta`; once the ETA row is `approved`,
      `outstanding` is empty and `state` is `ready`.

**Phase gate:** `pytest backend/tests -q` green. Commit. Record hash: `______`.

---

## Phase 4 — Loud, document-naming card badge + verify-manually (frontend)

**Objective.** Rewrite `readinessBadge` and the trip-card rendering so an
`action` trip shows a **loud, red** badge that **names the documents**
("⚠️ Need: ETA, Arrival card"), and an **imminent** trip in `unknown` state
shows "⚠️ Entry rules not verified — check before you fly." Demote `permit_type`
to a descriptive sub-line in the detail; lead with the documents.

**Narrative — why.** This is the fix the traveller actually sees. Naming the
documents (Phase 3's `outstanding`) is what turns "E-visa required" into "ETA
required". Treating imminent + unknown as loud closes the silent hole with zero
LLM cost.

**Assumptions to validate first:**
- [ ] `readinessBadge` (in `frontend/src/lib/immigration.ts`) is consumed by
      both `Trips.tsx` (card) and `TripDetail.tsx` — changing its shape touches
      both; check the call sites.
- [ ] The trip card has `start_date` available (it does: `TripSummary.start_date`)
      so imminence can be computed client-side. Pick a threshold — **≤ 30 days =
      imminent** (loud); confirm this reads well against the current trips.
- [ ] `REQUIREMENT_KIND_LABEL` already has friendly names for every kind — reuse
      it so the badge and checklist word documents identically.

**Gotchas / risks:**
- Use `parseDate` from `lib/format.ts` for any date math — never `new Date("…")`
  (README §6, the UTC-midnight trap).
- The badge only shows for non-past trips today (`Trips.tsx:80`). Keep that; a
  past trip needs no warning. But an **ongoing** trip (like London now) must
  still show it.
- Keep the discrepancy banner behaviour (README §1, decision 3) intact — it is
  the one thing that still renders on an otherwise-quiet past trip.
- Colours: reuse the existing `readiness-action` / `readiness-ready` /
  `readiness-unknown` palette; make `action` visually loud (the traveller asked
  for "big and red"), don't invent new tokens without checking the CSS.

**Tasks:**
- [ ] Change `readinessBadge` to take what it needs to know imminence (e.g. pass
      `daysUntil` or `startDate`), and to build `action` text from
      `readiness.outstanding` names, falling back to "Action needed" only if the
      list is somehow empty.
- [ ] Handle `unknown`: if imminent → loud verify-manually badge; if not
      imminent → keep the quiet "Not checked yet".
- [ ] Update `Trips.tsx` to pass the date and to style the loud state.
- [ ] In `TripDetail.tsx`'s `ReadinessSection`, lead with the outstanding
      documents; move `permitSummary` to a muted descriptive line.
- [ ] Update `frontend/src/lib/immigration.test.ts` (and any TripDetail test)
      for the new badge text and the imminent-unknown case.

**Phase gate:** `npm test` green; `npx tsc --noEmit` clean. Commit.
Record hash: `______`.

---

## Phase 5 — Verify against real data, deploy, document

**Objective.** Prove the London card now names the ETA, ship it, update the
README.

**Narrative — why.** Every input bug in this project was found by driving the
real UI (README §3, §8). The local dev server pulls the production DB, so the
actual London trip is the test fixture.

**Assumptions to validate first:**
- [ ] Local backend started via `scripts/dev_backend.py` (pulls prod DB); local
      frontend on :5173. No passkey needed on localhost.

**Gotchas / risks:**
- After Phase 2's override, the London reading only corrects once
  `sync_requirements` runs for that trip. Locally that happens on the next
  mutation; to force it without editing data, confirm the readiness read applies
  the override (`cached_policy` short-circuits to it) even before a re-sync — if
  the stale `visa` row lingers in the pulled DB, note it and confirm it retires
  on the next sync.
- **Do not skip the deploy** (README §4). The traveller pushes; then run
  `deploy.sh` over SSH, then grep the shipped bundle for a new string.
- `data/` override changes need the image rebuild (`deploy.sh`), not just a pull.

**Tasks:**
- [ ] Drive the Trips list in the browser: London shows a loud badge naming
      **ETA** (not "E-visa required"); screenshot it.
- [ ] Confirm a visa-free-nothing-owed trip stays quiet, and an imminent
      unchecked trip warns.
- [ ] Ask the traveller to `git push` (you cannot push).
- [ ] After push: `ssh yayokun@5.78.184.240 'cd /srv/yayo_travel_tracker && ./deploy/deploy.sh'`
- [ ] Verify the deployed bundle contains a new string from the badge change.
- [ ] Update README §1 (immigration readiness) to describe the document-naming
      badge, the imminent-unknown warning, and the documents-first policy call.

**Phase gate:** deployed, bundle verified, README updated. Commit.
Record hash: `______`.

---

## Explicitly out of scope (so nobody "improves" it by mistake)

- The no-refresh rule for cached policies (README §1) — unchanged.
- The Gmail accept boundary (README §5) — unchanged.
- A global cross-tab banner — the traveller chose card badges instead.
- Background policy prefetch — the traveller chose the zero-LLM-cost
  verify-manually path instead.
- Passport-expiry warnings — a separate idea, not this plan.

## Lessons learned (fill in as phases complete)

- _(append notable findings here as you go, per the project's plan convention)_
