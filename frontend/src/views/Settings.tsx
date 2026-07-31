import { useEffect, useState } from "react";

import { Field, Row, Select, Text } from "../components/Fields";
import { Sheet } from "../components/Sheet";
import { api, ApiError } from "../lib/api";
import { countryFlag, daysFromToday, formatDate } from "../lib/format";
import type { Nationality, Passport } from "../types";

interface Props {
  passports: Passport[];
  onPassportsChanged: () => void;
  onLoggedOut: () => void;
}

export function Settings({ passports, onPassportsChanged, onLoggedOut }: Props) {
  const [adding, setAdding] = useState(false);
  const [nationality, setNationality] = useState<Nationality>("US");
  const [last4, setLast4] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passkeys, setPasskeys] = useState<
    { id: number; nickname: string; created_at: string; last_used_at: string | null }[]
  >([]);
  const [codesLeft, setCodesLeft] = useState<number | null>(null);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);

  useEffect(() => {
    api.auth.passkeys().then(setPasskeys).catch(() => {});
    api.auth.status().then((s) => setCodesLeft(s.recovery_codes_left)).catch(() => {});
  }, []);

  async function addPassport() {
    setBusy(true);
    setError(null);
    try {
      await api.passports.create({
        nationality,
        number_last4: last4,
        expires_on: expires || null,
        is_default: passports.length === 0,
      });
      setAdding(false);
      setLast4("");
      setExpires("");
      onPassportsChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings">
      <h2>Settings</h2>

      <section>
        <div className="section-head">
          <h3>Passports</h3>
          <button className="btn btn-sm" onClick={() => setAdding(true)}>
            Add passport
          </button>
        </div>
        <p className="muted small">
          Only the last four characters of the number are ever stored. The app
          needs to tell you which document to carry, not to hold the document.
        </p>

        {passports.length === 0 && <p className="empty">None added yet.</p>}

        {passports.map((p) => {
          const days = daysFromToday(p.expires_on);
          // Most countries refuse entry inside six months of expiry.
          const expiringSoon = days !== null && days < 190;
          return (
            <div className="item-row" key={p.id}>
              <span className="flag">{countryFlag(p.nationality)}</span>
              <div className="entry-main">
                <strong>
                  {p.nationality}
                  {p.number_last4 ? ` ····${p.number_last4}` : ""}
                  {p.is_default ? " · default" : ""}
                </strong>
                <span className={expiringSoon ? "warn" : "muted"}>
                  {p.expires_on
                    ? `Expires ${formatDate(p.expires_on)}${
                        expiringSoon ? " — inside the 6-month window" : ""
                      }`
                    : "No expiry recorded"}
                </span>
                {p.countries_entered && p.countries_entered.length > 0 && (
                  <span className="muted">
                    Used for {p.countries_entered.join(", ")}
                  </span>
                )}
              </div>
              <div className="item-actions">
                {!p.is_default && (
                  <button
                    className="btn btn-sm"
                    onClick={async () => {
                      await api.passports.update(p.id, { is_default: true });
                      onPassportsChanged();
                    }}
                  >
                    Make default
                  </button>
                )}
                <button
                  className="icon-btn"
                  aria-label="Delete passport"
                  onClick={async () => {
                    if (
                      confirm(
                        `Remove the ${p.nationality} passport? Trips keep their history; they just stop showing which document you used.`,
                      )
                    ) {
                      await api.passports.remove(p.id);
                      onPassportsChanged();
                    }
                  }}
                >
                  🗑
                </button>
              </div>
            </div>
          );
        })}
      </section>

      <section>
        <h3>Passkeys</h3>
        {passkeys.map((k) => (
          <div className="item-row" key={k.id}>
            <div className="entry-main">
              <strong>{k.nickname || "Unnamed device"}</strong>
              <span className="muted">
                Added {formatDate(k.created_at.slice(0, 10))}
                {k.last_used_at
                  ? ` · last used ${formatDate(k.last_used_at.slice(0, 10))}`
                  : " · never used"}
              </span>
            </div>
            <button
              className="icon-btn"
              aria-label="Remove passkey"
              onClick={async () => {
                try {
                  await api.auth.deletePasskey(k.id);
                  setPasskeys(await api.auth.passkeys());
                } catch (e) {
                  alert(e instanceof ApiError ? e.message : String(e));
                }
              }}
            >
              🗑
            </button>
          </div>
        ))}
        <p className="muted small">
          Recovery codes remaining: {codesLeft ?? "—"}
        </p>
        <button
          className="btn btn-sm"
          onClick={async () => {
            if (
              confirm(
                "Generate a new set of 10 codes? Every existing code stops working immediately.",
              )
            ) {
              const r = await api.auth.regenerateRecoveryCodes();
              setNewCodes(r.recovery_codes);
              setCodesLeft(r.recovery_codes.length);
            }
          }}
        >
          Regenerate recovery codes
        </button>
      </section>

      <section>
        <h3>Session</h3>
        <button
          className="btn btn-sm"
          onClick={async () => {
            await api.auth.logout();
            onLoggedOut();
          }}
        >
          Sign out
        </button>
      </section>

      {adding && (
        <Sheet
          title="Add passport"
          onClose={() => setAdding(false)}
          footer={
            <>
              <button className="btn" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={addPassport}>
                {busy ? "Saving…" : "Save"}
              </button>
            </>
          }
        >
          {error && <p className="alert alert-danger">{error}</p>}
          <Row>
            <Field label="Nationality">
              <Select
                value={nationality}
                onChange={setNationality}
                options={[
                  { value: "MX", label: "Mexican" },
                  { value: "US", label: "United States" },
                ]}
              />
            </Field>
            <Field label="Last 4" hint="Never the full number">
              <Text value={last4} onChange={(v) => setLast4(v.slice(0, 4))} maxLength={4} />
            </Field>
          </Row>
          <Field label="Expires" wide>
            <input
              type="date"
              value={expires}
              onChange={(e) => setExpires(e.target.value)}
            />
          </Field>
        </Sheet>
      )}

      {newCodes && (
        <Sheet title="New recovery codes" onClose={() => setNewCodes(null)}>
          <p className="muted">
            Shown once. The previous set no longer works.
          </p>
          <ol className="codes">
            {newCodes.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ol>
          <button
            className="btn"
            onClick={() => navigator.clipboard?.writeText(newCodes.join("\n"))}
          >
            Copy all
          </button>
        </Sheet>
      )}
    </div>
  );
}
