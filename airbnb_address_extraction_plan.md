# Airbnb bookings and exact stay addresses — extraction plan

## Why this plan exists

Two gaps in the Gmail booking extractor:

1. **Airbnb emails are not handled deliberately.** `airbnb.com` is already on
   the sender allow-list (`data/rules/email-filter.json`), but every prompt in
   [`extraction.py`](backend/app/services/extraction.py) talks about "a hotel
   stay". Real Airbnb mail (sampled from the user's inbox on 2026-09-13) shows
   three traps the current prompts do nothing about:
   - **Not-yet-bookings pass the keyword filter.** "Invitation to book Karolyn's
     place", "Reminder to book…", and "RE: Inquiry at…" all contain
     `reservation` / `check in` in the body, so they reach triage. Triage must
     reject them — they are not a reservation the traveler holds.
   - **The footer carries Airbnb's own corporate address** (`888 Brannan St, San
     Francisco, CA 94103`, plus Airbnb Ireland in Dublin). A naive "address"
     field would capture that instead of the rental.
   - **The listing title is the "hotel name".** An Airbnb has no hotel; its
     listing title (e.g. `Central of Shibuya 5 min/Cozy Room`) is what names the
     stay, and a named stay is what the calendar draws as confirmed.
2. **The stay address is thrown away.** `Stay.address` already exists as a DB
   column (and in `StayCreate`/`StayUpdate`/`types.ts`), but the extraction
   schema has no `address` slot, so it is never filled. The user wants the
   **exact** address of the hotel or Airbnb recorded.

Like the flight-detail plan (`booking_detail_extraction_plan.md`), this widens
one chain end to end — miss a link and the field is silently dropped:

```
schema → Booking dataclass → validate_booking → Booking.payload()
       → _apply_hotel (accept) → ALLOWED_OVERRIDES + AcceptPayload → UI
```

## Decisions (do not re-litigate)

1. **Airbnb (and any vacation rental) is `kind: "hotel"`.** No new booking
   kind: a stay is a stay, and the whole review/calendar path already keys off
   `hotel`. `hotel_name` = the listing title.
2. **Address is recorded verbatim.** "Exact" means as printed in the
   confirmation's stay/itinerary section — street, unit, city, postal code,
   country — with line breaks joined by `, `. No reformatting, no geocoding,
   no translation. When an email prints the address in two scripts (e.g. a
   Japanese and an English block), take the one in the itinerary/listing
   section, not ones repeated in host directions.
3. **Never a platform's or chain's corporate address.** Footer addresses for
   Airbnb, Booking.com, Expedia, etc. are not the stay. If the only address in
   the email is a footer one, `address` is `null`.
4. **Nothing else from host notes is captured.** Door codes, Wi-Fi passwords,
   lockbox PINs appear in these emails; they are deliberately not extracted or
   stored.
5. **No geocoding change.** `fill_coordinates` keeps using the city. Geocoding
   the address is a separate, later decision.

## Cross-cutting gotchas (true for every phase)

- **Strict tool schema:** every new property must be in both `properties` and
  `required`, with optionality expressed as `["string", "null"]`.
- **Old proposals predate the field.** Stored `payload_json` rows have no
  `address` key — `payload.get("address")` must tolerate absence, and the
  frontend type must be optional *and* nullable.
- **`address` is detail, not load-bearing.** Like `seat`, a bad address never
  rejects a booking; `_clean_str` (trim, empty → null) is enough.
- **Two override gates.** `AcceptPayload` (pydantic, `api/review.py`) drops
  undeclared fields *before* `ALLOWED_OVERRIDES` (`services/review.py`) is
  consulted. Add `address` to both.
- **Privacy:** tests use synthetic fixtures modeled on the real email's
  *structure*. Never commit real addresses, names, or codes from the inbox.
- **Shell:** Windows PowerShell 5.1 — one command per line, no `&&`.

---

## Phase 1 — Capture the address and teach the prompts about Airbnb

**Why:** this is where the information is lost today. Until the schema has an
`address` slot and the prompts know what an Airbnb confirmation looks like,
nothing downstream has anything to store.

**Objective:** a fake model returning an Airbnb-shaped booking with an address
round-trips through `validate_bookings` → `Booking.payload()` intact, and the
prompt text encodes Decisions 1–4.

**Assumptions to validate first:**
- [x] `BOOKING_ITEM_SCHEMA` is still the only extract schema (grep
      `input_schema`). — confirmed (the other two are triage and immigration).
- [x] `Booking.payload()` copies `__dict__`, so a new dataclass field reaches
      `payload_json` with no extra code. — confirmed by test.
- [x] `airbnb.com` is on `allow_sender_domains` and a modern Airbnb
      confirmation subject ("Reservation confirmed - …") passes `classify`.
      — confirmed; `invitation@`, `automated@`, `express@` all ride on the
      domain rule.

**Common problems to prepare for:**
- Forgetting `address` in `required` → OpenRouter strict-mode rejects the tool.
- A frozen dataclass field without a default breaks every existing
  `Booking(...)` construction — default it to `None`.
- Over-steering the triage prompt could make it reject real hotel mail. Keep
  the change additive: list what is *not* a booking, don't redefine what is.

**Tasks:**
- [x] Add `address` (nullable string) to `BOOKING_ITEM_SCHEMA` properties and
      `required`, with a description encoding Decisions 2–4.
- [x] Update the `hotel_name` description: for a vacation rental (Airbnb,
      Vrbo) it is the listing title.
- [x] Update the `kind` description: a vacation rental is `hotel`.
- [x] Update `TRIAGE_TOOL.is_booking`: vacation rentals count; inquiries,
      invitations/pre-approvals to book, pending booking requests, reminders
      to finish booking, and messages from a host are **not** bookings.
- [x] Add `address: Optional[str] = None` to `Booking`; clean it in
      `validate_booking`. — used a dedicated `_clean_address` instead of
      `_clean_str`: it collapses stray whitespace/newlines and returns None for
      a non-string rather than raising (`_clean_str` raising would have
      rejected the whole booking over a malformed address).
- [x] Add an email-filter test proving a synthetic Airbnb confirmation from
      `automated@airbnb.com` is a candidate.

**Tests that must pass to proceed:**
- [x] Airbnb-shaped booking (listing title, address) survives
      `validate_bookings` and appears in `payload()` — and in the stored
      `payload_json` via `run_extractions`.
- [x] A payload with no `address` key still validates (old proposals).
- [x] Whitespace-only / non-string address → `None`; booking still kept.
- [x] Schema test: every property is in `required` (strict-mode guard).
- [x] Prompt-text guard: the footer warning and the triage exclusions stay put.
- [x] Full backend suite green (**379 passed**), `ruff check app tests` clean.
      (`ruff check .` reports 2 pre-existing unused imports in alembic
      migrations — not touched here.)

**Commit:** _(pending)_

---

## Phase 2 — Write the address onto the Stay on accept

**Why:** capturing is invisible until accepting a proposal puts the address on
the real `Stay`. The reviewer must also be able to fix a wrong address before
accept.

**Objective:** accepting a hotel/Airbnb proposal fills `Stay.address`; an
`address` override from the Review form (service and HTTP) lands on the Stay.

**Assumptions to validate first:**
- [ ] `_apply_hotel` is the only place a booking becomes a `Stay`.
- [ ] `Stay.address` is `str = ""` (non-null) — so `None` must become `""`.

**Common problems to prepare for:**
- Writing `None` into a non-null column → integrity error on commit. Use
  `booking.address or ""`.
- Adding to `ALLOWED_OVERRIDES` but not `AcceptPayload` (or vice versa) — the
  override silently vanishes. Test through the HTTP API, not just the service.

**Tasks:**
- [ ] `_apply_hotel`: `address=booking.address or ""`.
- [ ] Add `address` to `ALLOWED_OVERRIDES` and `AcceptPayload`.

**Tests that must pass to proceed:**
- [ ] Accepting a hotel with an address creates a Stay with that exact string.
- [ ] Accepting a hotel without an address stores `""`.
- [ ] An `address` override via `POST /api/review/{id}/accept` lands on the Stay.
- [ ] Full backend suite green, `ruff check` clean.

**Commit:** _(pending)_

---

## Phase 3 — Show and edit the address in the UI

**Why:** the user wants the address *recorded*, which means visible where they
look at a stay and correctable if the model misread it.

**Objective:** the Review card shows an editable Address field for hotel
proposals; the stay form has an Address field (in its folded "Hotel details"
section); the trip's hotel row shows the address.

**Assumptions to validate first:**
- [ ] `Stay` in `types.ts` already has `address: string`.
- [ ] `draftToPayload` spreads the whole draft, so adding `address` to
      `StayDraft` is enough for create/update to send it.
- [ ] The calendar's trip-list payload deliberately omits address
      (`api/trips.py`) — leave that alone; the trip detail fetch has it.

**Common problems to prepare for:**
- `ReviewBooking.address` must be optional + nullable (old proposals).
- Adding `address` to `StayDraft` means every `StayDraft` literal (tests,
  `emptyStay`, `stayToDraft`) needs it or `tsc` fails the build.
- Long addresses must wrap, not stretch the row.

**Tasks:**
- [ ] `types.ts`: `ReviewBooking.address?: string | null`.
- [ ] `Review.tsx`: wide `Address` text field under `Hotel` for hotel cards.
- [ ] `StayForm.tsx`: `address` in `StayDraft`, `emptyStay`, `stayToDraft`;
      `Address` field in the folded section (open it when address is set);
      fix the header comment that says address is absent from the form.
- [ ] `TripDetail.tsx` `StayRow`: show the address when present.

**Tests that must pass to proceed:**
- [ ] `Review.test.tsx`: a hotel proposal renders its address in the field.
- [ ] `TripDetail.test.tsx`: a stay with an address shows it.
- [ ] `npm test` and `npm run build` (tsc + vite) green.

**Commit:** _(pending)_

---

## Phase 4 — Deploy and verify against the real model

**Why:** unit tests use a fake model; only the real Sonnet-via-OpenRouter call
proves the prompts steer it away from the footer address and reject
invitations. There is no OpenRouter key on the dev machine, so this runs
inside the deployed container, read-only.

**Assumptions to validate first:**
- [ ] The user has pushed `main` (pushing is the user's call).
- [ ] The container can run a one-off Python snippet with the prod key.

**Common problems to prepare for:**
- `data/` and code are baked into the image — `git pull` alone does nothing;
  run `./deploy/deploy.sh`.
- Do **not** accept proposals in prod as a test; verify with a read-only
  `model.extract(...)` call on synthetic emails and print the result.

**Tasks:**
- [ ] Deploy with `./deploy/deploy.sh`; confirm healthy.
- [ ] Read-only run on a synthetic Airbnb confirmation (with a footer corporate
      address and a host-notes address block): `address` is the listing
      address, not 888 Brannan St.
- [ ] Read-only triage on a synthetic "Invitation to book" → `is_booking=false`.
- [ ] Update README §5 with the address field and the Airbnb notes; record
      lessons learned here.

**Commit:** _(pending)_

---

## Notes for future engineers

_(filled in as phases complete)_

## Lessons learned

_(filled in as phases complete)_
