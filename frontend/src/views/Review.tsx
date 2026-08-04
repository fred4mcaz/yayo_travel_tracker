import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { countryFlag, formatDateTime, formatRange } from "../lib/format";
import type { RecentEmail, ReviewBooking, ReviewItem } from "../types";
import { Field, Row, Text } from "../components/Fields";

interface Props {
  onReviewed: () => void;
}

/** The review queue: proposals extracted from email, awaiting a decision.
 *
 *  Nothing here writes trip data on its own -- the two buttons post to the
 *  accept and reject endpoints, and only accept touches a trip. A proposal the
 *  model read incompletely can be corrected inline before it is accepted. */
export function ReviewQueue({ onReviewed }: Props) {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [pollNote, setPollNote] = useState<string | null>(null);
  const [learnedNote, setLearnedNote] = useState<string | null>(null);
  const [emailsOpen, setEmailsOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems(await api.review.list());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  async function checkNow() {
    setPolling(true);
    setPollNote(null);
    setError(null);
    try {
      const r = await api.review.poll();
      const found = r.extraction.proposed;
      setPollNote(
        r.ingest.baselined
          ? "First check done — future bookings will show up here."
          : found > 0
            ? `Found ${found} new booking${found === 1 ? "" : "s"}.`
            : "No new bookings.",
      );
      await load();
    } catch (e) {
      // A 409 here is the gate speaking: ingest is off or unconfigured.
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPolling(false);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  const afterDecision = useCallback(
    (learnedDomain?: string | null) => {
      void load();
      onReviewed();
      if (learnedDomain) {
        setLearnedNote(
          `Learned sender: future email from ${learnedDomain} will be flagged automatically.`,
        );
      }
    },
    [load, onReviewed],
  );

  const afterExtract = useCallback(() => {
    void load();
  }, [load]);

  if (items === null) {
    return (
      <div className="review">
        <span className="pill">Loading…</span>
      </div>
    );
  }

  return (
    <div className="review">
      <div className="section-head">
        <h2>Review</h2>
        <div className="review-head-right">
          {items.length > 0 && (
            <span className="pill">{items.length} waiting</span>
          )}
          <button
            className="btn btn-sm"
            onClick={() => setEmailsOpen((open) => !open)}
            aria-expanded={emailsOpen}
          >
            {emailsOpen ? "Hide recent emails" : "Find recent emails"}
          </button>
          <button
            className="btn btn-sm"
            onClick={checkNow}
            disabled={polling}
          >
            {polling ? "Checking…" : "Check email now"}
          </button>
        </div>
      </div>

      {emailsOpen && <RecentEmailsPanel onExtracted={afterExtract} />}

      {pollNote && <p className="review-note muted">{pollNote}</p>}
      {learnedNote && <p className="review-note muted">{learnedNote}</p>}
      {error && <p className="alert alert-danger">{error}</p>}

      {items.length === 0 ? (
        <div className="empty-state">
          <p>Nothing to review.</p>
          <p className="muted">
            Bookings found in your email will appear here for you to accept
            before anything is added to a trip.
          </p>
        </div>
      ) : (
        <div className="review-list">
          {items.map((item) => (
            <ReviewCard key={item.id} item={item} onDone={afterDecision} />
          ))}
        </div>
      )}
    </div>
  );
}

/** The manual safety net (D2): every stored email from the last few days,
 *  flagged or not, so one can be picked for extraction even though the
 *  automatic filter never touched it. Read-only until "Extract" is pressed --
 *  no bulk sends, and picking one is a deliberate, per-message choice. */
function RecentEmailsPanel({ onExtracted }: { onExtracted: () => void }) {
  const [emails, setEmails] = useState<RecentEmail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [extractingId, setExtractingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setEmails(await api.review.recentEmails());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function extract(id: number) {
    setExtractingId(id);
    setError(null);
    try {
      await api.review.extractEmail(id);
      onExtracted();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setExtractingId(null);
    }
  }

  return (
    <div className="recent-emails-panel">
      <p className="review-note muted">
        Picking an email here sends that one message to the extractor, even
        if it wasn't automatically flagged.
      </p>
      {error && <p className="alert alert-danger">{error}</p>}
      {emails === null ? (
        <p className="muted">Loading…</p>
      ) : emails.length === 0 ? (
        <p className="muted">No email in the last few days.</p>
      ) : (
        <div className="recent-email-list">
          {emails.map((e) => (
            <div className="recent-email-row" key={e.id}>
              <div className="recent-email-main">
                <div className="recent-email-subject">
                  {e.subject || "(no subject)"}
                </div>
                <div className="muted">
                  {e.from_addr}
                  {e.received_at && ` · ${formatDateTime(e.received_at)}`}
                </div>
                {e.snippet && <div className="review-snippet">{e.snippet}</div>}
              </div>
              <div className="recent-email-side">
                <div className="recent-email-badges">
                  {e.looks_like_travel && (
                    <span className="pill" title="Matched the automatic filter">
                      Flagged
                    </span>
                  )}
                  {e.has_pending && (
                    <span className="pill" title="Already sent for extraction">
                      Extracted
                    </span>
                  )}
                </div>
                {!e.has_pending && (
                  <button
                    className="btn btn-sm"
                    onClick={() => extract(e.id)}
                    disabled={extractingId !== null}
                  >
                    {extractingId === e.id ? "Extracting…" : "Extract"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  hotel: "Hotel",
  flight: "Flight",
  train: "Train",
  bus: "Bus",
  ferry: "Ferry",
  car: "Car",
  other: "Booking",
};

function ReviewCard({
  item,
  onDone,
}: {
  item: ReviewItem;
  onDone: (learnedDomain?: string | null) => void;
}) {
  const [edit, setEdit] = useState<Partial<ReviewBooking>>({});
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const b = item.booking;
  const isHotel = b.kind === "hotel";
  // Field value: the reviewer's edit if they've touched it, else the model's.
  const val = (k: keyof ReviewBooking): string =>
    (edit[k] ?? b[k] ?? "") as string;
  const set = (k: keyof ReviewBooking, v: string) =>
    setEdit((e) => ({ ...e, [k]: v }));

  async function accept() {
    setBusy("accept");
    setError(null);
    try {
      // Send only the fields the reviewer actually changed.
      const overrides = Object.fromEntries(
        Object.entries(edit).filter(([, v]) => v !== undefined && v !== ""),
      );
      const res = await api.review.accept(item.id, overrides);
      onDone(res.learned_domain);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      await api.review.reject(item.id);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  }

  const country = val("country_code");

  return (
    <div className="review-card">
      {/* Actions live at the top of the card so a long list of proposals never
          forces a scroll to reach Accept/Dismiss. */}
      <div className="review-card-head">
        <span className="review-kind">
          {country && <span className="flag">{countryFlag(country)}</span>}
          {KIND_LABEL[b.kind] ?? "Booking"}
          {b.country_name && <span className="muted"> · {b.country_name}</span>}
          {item.confidence !== null && (
            <span className="pill" title="Model confidence">
              {Math.round(item.confidence * 100)}%
            </span>
          )}
        </span>
        <div className="review-card-actions">
          <button
            className="btn btn-primary btn-sm"
            onClick={accept}
            disabled={busy !== null}
          >
            {busy === "accept" ? "…" : "Accept"}
          </button>
          <button className="btn btn-link" onClick={reject} disabled={busy !== null}>
            {busy === "reject" ? "…" : "Dismiss"}
          </button>
        </div>
      </div>

      <div className="review-destination">
        {item.suggestion ? (
          <>Joins <strong>{item.suggestion.label}</strong></>
        ) : (
          <>Creates a <strong>new trip</strong></>
        )}
        {" · "}
        <span className="review-when muted">
          {formatRange(val("start_date") || null, val("end_date") || null)}
        </span>
      </div>

      {error && <p className="alert alert-danger">{error}</p>}

      <div className="review-email">
        <div className="review-subject">{item.email.subject || "(no subject)"}</div>
        <div className="muted">
          {item.email.from_addr}
          {item.email.received_at && ` · ${formatDateTime(item.email.received_at)}`}
        </div>
        {item.email.snippet && (
          <div className="review-snippet">{item.email.snippet}</div>
        )}
      </div>

      <div className="review-fields">
        <Row>
          <Field label="Country">
            <Text
              value={country}
              onChange={(v) => set("country_code", v.toUpperCase())}
              placeholder="e.g. VN"
              maxLength={2}
            />
          </Field>
          {isHotel && (
            <Field label="City">
              <Text value={val("city")} onChange={(v) => set("city", v)} />
            </Field>
          )}
        </Row>
        <Row>
          <Field label={isHotel ? "Check-in" : "Departs"}>
            <Text
              value={val("start_date")}
              onChange={(v) => set("start_date", v)}
              type="date"
            />
          </Field>
          {isHotel && (
            <Field label="Check-out">
              <Text
                value={val("end_date")}
                onChange={(v) => set("end_date", v)}
                type="date"
              />
            </Field>
          )}
        </Row>
        {isHotel ? (
          <Field label="Hotel" wide>
            <Text value={val("hotel_name")} onChange={(v) => set("hotel_name", v)} />
          </Field>
        ) : (
          <Field label="Carrier" wide>
            <Text value={val("carrier")} onChange={(v) => set("carrier", v)} />
          </Field>
        )}
      </div>
    </div>
  );
}
