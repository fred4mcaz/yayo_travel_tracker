# Missed-Email Review Plan

Catch travel bookings the automatic filter didn't flag, and teach the filter to
recognize their senders next time. Also fixes the root cause that let the redBus
ferry ticket slip through (HTML-only body), and recovers that specific email.

## Background / why this exists

The redBus ferry confirmation (`ticketmaster@redbus.sg`, 2026-08-04) was never
flagged for review, for two independent reasons:

1. **Timing** — it arrived (~14:41 UTC) before `redbus.sg` was added to the
   allow-list and deployed (~14:53 UTC). Classification is one-shot
   (`email_ingest._store`), so the frozen `looks_like_travel=False` is never
   recomputed.
2. **HTML-only body** — the ingester reads only the plain-text part
   (`ImapMailbox.fetch_after`, `body=msg.text`). redBus sends `text/html` + a
   PDF, no `text/plain`, so the body handed to `classify()` is empty. The
   confirmation-keyword gate then can't match ("PNR", "Ticket Number" all live
   in the HTML), and the subject alone carries no keyword. **The domain fix
   alone does not make future redBus mail pass.**

Beyond the fix, the user wants a manual safety-net: a Review-page button that
lists recent emails, lets one be selected for extraction even if it was never
flagged, and records what made it a booking (sender, keyword) so the automatic
filter catches similar mail in future.

## Design decisions (please confirm before Phase 3+)

- **D1 — Learned rules live in the database, not the committed JSON.** The JSON
  under `data/rules/` is read-only and baked into the image. A new `LearnedRule`
  table (Alembic migration) holds runtime-added sender domains (and optionally a
  subject keyword). `email_filter` merges committed rules ∪ learned rules.
- **D2 — Manual extraction is an explicit per-message consent.** Selecting an
  email deliberately sends *that one message* to the LLM extractor, overriding
  the automatic sender gate. This is a conscious, logged relaxation of the
  privacy control, initiated by the operator per message — never automatic.
- **D3 — Learn the sender *domain* only, and only on accept.** Manually
  extracting proves nothing; accepting the resulting booking does. Learning on
  accept avoids polluting the allow-list with senders that produced garbage.
  **No per-sender keyword learning** — instead, a learned (or committed) sender's
  future mail is flagged by the normal confirmation-keyword gate, and that
  shared keyword list is broadened to cover common booking words the user named
  ("reservation", "ticket", "confirmation", …). "reservation"/"confirmation" are
  already present; **"ticket" is added** (see Phase 1). So `LearnedRule` stores a
  domain, nothing more.
- **D4 — Recover the ferry by re-fetching its full body**, not by trusting the
  empty stored snippet.

---

## Phase 1 — HTML-body fallback in the ingester

**Objective.** When a message has no `text/plain` part, derive the body (for both
classification and the stored snippet) from the `text/html` part with tags
stripped. Makes redBus and every other HTML-only sender classifiable going
forward.

**Narrative.** This is the root-cause fix. Everything else (recovery, the manual
UI, learning) is worth more once the pipeline can actually see HTML-only ticket
bodies. Small, self-contained, and independently shippable.

**Assumptions to validate.**
- [x] `imap_tools` `MailMessage.html` holds the HTML part when `.text` is empty
      (confirm against the installed version). Confirmed against installed
      `imap_tools==1.7.4`: `.html` walks MIME parts for `text/html` and returns
      `""` when absent, same shape as `.text`.
- [x] No HTML-parsing dependency (e.g. bs4) is currently vendored; a
      dependency-free `html.unescape` + regex tag-strip is acceptable here.
      Confirmed — no bs4/lxml in `backend/.venv`; used stdlib `html.unescape`
      plus two compiled regexes.

**Gotchas / risks.**
- Over-stripping: `<style>`/`<script>` blocks must be dropped whole, not just
  their tags, or CSS text pollutes the snippet (the redBus email has a large
  `<style>` block). Strip those elements' contents first.
- Whitespace: collapse runs to single spaces (the existing `_snippet` already
  does this downstream, but the classifier reads the full body).
- Keep it plain-text only — never send HTML onward; this feeds the local
  keyword check and the snippet, nothing else.

**Tasks.**
- [x] Add `email_ingest._html_to_text(html)` — drop `<style>/<script>` bodies,
      strip tags, unescape entities, collapse whitespace.
- [x] In `ImapMailbox.fetch_after`, set `body = msg.text or _html_to_text(msg.html)`.
- [x] `test_email_ingest`: HTML-only fake message yields a non-empty body/snippet.
- [x] `test_email_filter` (or ingest test): a redBus-shaped fixture
      (`ticketmaster@redbus.sg`, HTML body containing "PNR") classifies as a
      candidate.
- [x] Broaden `confirmation_keywords` in `data/rules/email-filter.json` to add
      "ticket" (verify "reservation"/"confirmation"/"itinerary" already present);
      add a test that a "ticket"-only subject from an allow-listed sender passes.
- [x] Full backend suite green.

**Lessons learned.** `<p>...</p>` boundaries collapse to a single space once
tags are stripped, so text that hugs a closing tag (`AB12CD</b>.`) gets a space
inserted before trailing punctuation. Not a bug — just means snippet-level
assertions in tests should not expect punctuation to hug the preceding word.

**Exit test.** `pytest backend/tests/test_email_ingest.py backend/tests/test_email_filter.py`
plus the whole suite pass (224 passed). Commit. Hash: `8676747`

---

## Phase 2 — Recover the already-ingested ferry email

**Objective.** Get the specific redBus ferry booking into the review queue.

**Narrative.** Phase 1 fixes the future; this one message is already stored with
an empty snippet and a frozen `False` flag, so it needs a targeted one-off.

**Assumptions to validate.**
- [ ] The message still exists in the mailbox under a UID we can re-fetch, and
      UIDVALIDITY is unchanged (so the stored `imap_uid` is still valid).
- [ ] Its `EmailMessage` row exists (by `message_id`).

**Gotchas / risks.**
- The stored snippet is empty; re-derive it from a live re-fetch using Phase 1's
  fallback rather than hand-pasting text.
- If UIDVALIDITY changed, fall back to locating by `message_id` via IMAP search.

**Tasks.**
- [ ] `app/tasks/reflag_message.py` (management command): given a `message_id`,
      re-fetch the full body over IMAP, update `snippet`, set
      `looks_like_travel=True`, clear `processed_at`.
- [ ] Unit test against a fake mailbox.
- [ ] Run on the server for the ferry `message_id`, then a poll/extraction cycle;
      confirm a pending proposal appears (ferry, ID Batam→Malaysia).

**Exit test.** The ferry shows in `GET /api/review` as a pending proposal on the
server. Commit the command + test. Hash: ____

---

## Phase 3 — Runtime-learned filter rules (schema + filter merge)

**Objective.** Let the allow-list grow at runtime, merged with the committed JSON.

**Narrative.** The learning half of the feature needs somewhere mutable to learn
*into*. Building the store and the filter merge first means Phase 4 can just
write rows.

**Assumptions to validate.**
- [ ] Alembic is the migration path (confirmed: `backend/alembic/versions/`).
- [ ] `classify` can accept a merged `FilterRules` without disturbing its current
      pure/testable shape.

**Gotchas / risks.**
- `load_rules` is `@lru_cache`d on the committed file; learned rules are dynamic,
  so they must be fetched per-classification (cheap DB read) and unioned — do not
  cache the union.
- Domain normalization must match the committed path (lowercase, strip `@`,
  subdomain rule via `_domain_allowed`).

**Tasks.**
- [ ] Model `LearnedRule` (domain: str, source: str, created_at) + Alembic
      migration. Domain only — no keyword column (see D3).
- [ ] `email_filter.effective_rules(session)` → committed ∪ learned domains.
- [ ] `email_ingest._store` uses `effective_rules(session)`.
- [ ] Tests: a learned domain flips a previously-rejected sender to candidate;
      no DB rows → committed behavior unchanged.

**Exit test.** New filter tests + full suite pass. Commit. Hash: ____

---

## Phase 4 — Backend: recent emails, manual extract, learning

**Objective.** APIs to list the last N days of stored emails, extract a chosen
one on demand, and persist a learned sender on accept.

**Narrative.** The server-side of the safety-net. Reuses the existing extraction
model and the accept/reject queue so the frontend stays thin.

**Assumptions to validate.**
- [ ] All `/api/review/*` routes are behind the passkey wall (confirmed via
      existing router).
- [ ] Re-fetch-by-UID is feasible with `imap_tools` (add `fetch_uids` to the
      `Mailbox` protocol + `ImapMailbox`).

**Gotchas / risks.**
- Don't double-extract: if a pending Extraction already exists for the email,
  return it instead of making another.
- IMAP refetch failure (UIDVALIDITY change, message deleted) → fall back to the
  stored snippet and note reduced fidelity.
- Cost/rate: manual extract skips triage (user already decided) and calls the
  extract model once.

**Tasks.**
- [ ] `GET /api/review/recent-emails?days=3` → `[{id, from_addr, subject,
      snippet, received_at, looks_like_travel, has_pending}]`, newest first.
- [ ] `extraction.extract_selected(session, model, email, body)` → create pending
      Extraction (bypass the `looks_like_travel` gate; full body from refetch).
- [ ] `POST /api/review/emails/{id}/extract` → returns the serialized proposal.
- [ ] On accept (`services.review.accept_extraction`), if the source sender's
      domain isn't already covered, write a `LearnedRule` (domain only) and log it.
- [ ] Endpoint tests with fake mailbox + fake model.

**Exit test.** New API tests + full suite pass. Commit. Hash: ____

---

## Phase 5 — Frontend: "Catch a missed email" on the Review page

**Objective.** A button that lists recent emails, lets one be extracted, and
surfaces it in the queue; shows when a sender was learned.

**Narrative.** The user-facing payoff. Fits beside the existing "Check email now"
control in `Review.tsx`.

**Assumptions to validate.**
- [ ] `ReviewItem`/types extend cleanly for the recent-email list.
- [ ] The dev preview can exercise it (fake data or the seeded dev DB).

**Gotchas / risks.**
- Keep the list read-only until "Extract" is pressed — no bulk sends.
- Make the privacy point visible: a one-line note that extracting sends that
  message to the extractor.
- Loading/disabled states per row so double-clicks don't double-extract.

**Tasks.**
- [ ] `api.review.recentEmails(days)`, `api.review.extractEmail(id)` + types.
- [ ] Review.tsx: "Find recent emails" button → panel listing last 3 days
      (from, subject, snippet, flagged/extracted badges); per-row "Extract".
- [ ] On extract success: refresh the queue, reveal the new proposal.
- [ ] Learned-sender confirmation note after accept.
- [ ] Browser-preview verification (list renders, extract flows to queue).

**Exit test.** `npm run build` + typecheck clean; manual preview shows the flow.
Commit. Hash: ____

---

## Phase 6 — Deploy & verify

**Objective.** Ship all phases to the server and confirm end to end.

**Tasks.**
- [ ] Push `main`; run `./deploy/deploy.sh` on the server (pulls, backs up DB,
      rebuilds image, health-checks).
- [ ] Run the Phase 2 reflag command for the ferry; confirm it's in the queue.
- [ ] Verify the recent-emails button on the live site; extract a test email,
      accept it, confirm the sender is learned.
- [ ] Stamp commit hashes into this plan.

**Exit test.** Live site healthy; ferry reviewable; manual-catch flow works.
Hash: ____

---

## Notes for a future engineer

- Classification is one-shot at store time; re-flagging an old message means
  updating the row + clearing `processed_at`, not re-ingesting (dedup by
  `message_id` blocks re-ingest).
- `data/rules/email-filter.json` is read-only/baked into the image; anything
  learned at runtime must go through the DB (`LearnedRule`), never that file.
- Only `var/` is bind-mounted; any `data/` change requires an image rebuild via
  `deploy.sh`.
