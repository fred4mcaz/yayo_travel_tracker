import { useEffect, useState } from "react";

type Health = { status: string; rp_id: string; email_ingest_enabled: boolean };
type Probe =
  | { state: "loading" }
  | { state: "ok"; health: Health }
  | { state: "error"; message: string };

/** Stage 1 shell: confirms the browser can reach the API through Caddy.
 *  Stage 4 replaces this with the real layout. */
export function App() {
  const [probe, setProbe] = useState<Probe>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Health>;
      })
      .then((health) => !cancelled && setProbe({ state: "ok", health }))
      .catch((e: Error) => !cancelled && setProbe({ state: "error", message: e.message }));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="boot">
      <h1>Yayo travel</h1>
      {probe.state === "loading" && <span className="pill">Checking API…</span>}
      {probe.state === "ok" && (
        <>
          <span className="pill" data-state="ok">
            API reachable
          </span>
          <p>
            Serving as {probe.health.rp_id} · email ingest{" "}
            {probe.health.email_ingest_enabled ? "on" : "off"}
          </p>
        </>
      )}
      {probe.state === "error" && (
        <>
          <span className="pill" data-state="error">
            API unreachable
          </span>
          <p>{probe.message}</p>
        </>
      )}
    </div>
  );
}
