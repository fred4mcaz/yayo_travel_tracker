import { useState } from "react";

import { api, ApiError } from "../lib/api";
import * as webauthn from "../lib/webauthn";

interface Props {
  enrolled: boolean;
  enrollmentToken: string | null;
  onAuthenticated: () => void;
}

type Mode = "passkey" | "recovery";

export function Auth({ enrolled, enrollmentToken, onAuthenticated }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("passkey");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);

  const supported = webauthn.isSupported();
  // Enrolling needs the one-time token from the container log; after the first
  // passkey exists this screen is a plain login.
  const enrolling = !enrolled;

  async function handlePasskey() {
    setBusy(true);
    setError(null);
    try {
      if (enrolling) {
        if (!enrollmentToken) {
          setError(
            "No enrollment token in the URL. Open the link printed in the server log.",
          );
          return;
        }
        const { options } = await api.auth.registerBegin(enrollmentToken);
        const credential = await webauthn.register(options);
        const result = await api.auth.registerFinish(
          credential,
          navigator.platform || "this device",
        );
        if (result.recovery_codes) {
          // Shown exactly once. Don't navigate away until acknowledged.
          setNewCodes(result.recovery_codes);
          return;
        }
      } else {
        const { options } = await api.auth.loginBegin();
        const credential = await webauthn.authenticate(options);
        await api.auth.loginFinish(credential);
      }
      onAuthenticated();
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : webauthn.describeError(e),
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleRecovery(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.auth.recover(recoveryCode.trim());
      onAuthenticated();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (newCodes) {
    return <RecoveryCodes codes={newCodes} onDone={onAuthenticated} />;
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <h1>Yayo travel</h1>

        {!supported && (
          <p className="alert alert-danger">
            This browser cannot use passkeys. Use a recent Safari, Chrome, Edge,
            or Firefox.
          </p>
        )}

        {enrolling && (
          <p className="auth-lede">
            Set up the passkey that will unlock this site. Use a synced passkey
            (iCloud Keychain, Google Password Manager, 1Password) and every one
            of your devices is covered at once.
          </p>
        )}

        {error && <p className="alert alert-danger">{error}</p>}

        {mode === "passkey" ? (
          <>
            <button
              className="btn btn-primary btn-block"
              onClick={handlePasskey}
              disabled={busy || !supported}
            >
              {busy
                ? "Waiting for your device…"
                : enrolling
                  ? "Create passkey"
                  : "Sign in with passkey"}
            </button>
            {!enrolling && (
              <button className="btn-link" onClick={() => setMode("recovery")}>
                Lost your device? Use a recovery code
              </button>
            )}
          </>
        ) : (
          <form onSubmit={handleRecovery}>
            <label className="field">
              <span>Recovery code</span>
              <input
                value={recoveryCode}
                onChange={(e) => setRecoveryCode(e.target.value)}
                placeholder="a1b2c3d4-e5f6a7b8"
                autoComplete="one-time-code"
                spellCheck={false}
                autoFocus
              />
            </label>
            <button
              className="btn btn-primary btn-block"
              type="submit"
              disabled={busy || !recoveryCode.trim()}
            >
              {busy ? "Checking…" : "Use recovery code"}
            </button>
            <button
              type="button"
              className="btn-link"
              onClick={() => setMode("passkey")}
            >
              Back to passkey
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

function RecoveryCodes({
  codes,
  onDone,
}: {
  codes: string[];
  onDone: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <div className="auth">
      <div className="auth-card auth-card-wide">
        <h1>Save your recovery codes</h1>
        <p className="auth-lede">
          These are shown once and stored only as hashes — nobody, including
          this app, can recover them later. Each works a single time. Keep them
          somewhere that is <em>not</em> the password manager holding your
          passkey, or a lost device takes both.
        </p>

        <ol className="codes">
          {codes.map((code) => (
            <li key={code}>
              <code>{code}</code>
            </li>
          ))}
        </ol>

        <div className="row">
          <button
            className="btn"
            onClick={() => {
              navigator.clipboard?.writeText(codes.join("\n"));
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy all"}
          </button>
          <button
            className="btn"
            onClick={() => {
              const blob = new Blob(
                [
                  "Yayo travel — recovery codes\n",
                  "travel.foryayo.com\n",
                  `Generated ${new Date().toISOString().slice(0, 10)}\n\n`,
                  codes.join("\n"),
                  "\n\nEach code works once.\n",
                ],
                { type: "text/plain" },
              );
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = "yayo-travel-recovery-codes.txt";
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            Download
          </button>
        </div>

        <label className="check">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          <span>I have saved these somewhere safe</span>
        </label>

        <button
          className="btn btn-primary btn-block"
          disabled={!acknowledged}
          onClick={onDone}
        >
          Continue
        </button>
      </div>
    </div>
  );
}
