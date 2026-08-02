import type {
  AuthStatus,
  Note,
  Passport,
  ReviewItem,
  TripDetail,
  TripSummary,
} from "../types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    // Same-origin, but be explicit: the session cookie must ride along.
    credentials: "same-origin",
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const body = text ? JSON.parse(text) : null;

  if (!res.ok) {
    // FastAPI validation errors arrive as a list under `detail`; flatten them
    // into something a person can read rather than showing [object Object].
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail
          .map((d: { loc?: string[]; msg?: string }) =>
            `${d.loc?.slice(1).join(".") ?? "field"}: ${d.msg ?? "invalid"}`,
          )
          .join("; ")
      : (detail ?? `Request failed (${res.status})`);
    throw new ApiError(res.status, message);
  }
  return body as T;
}

const get = <T>(p: string) => request<T>(p);
const post = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) });
const patch = <T>(p: string, body: unknown) =>
  request<T>(p, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(p: string) => request<T>(p, { method: "DELETE" });

export const api = {
  auth: {
    status: () => get<AuthStatus>("/api/auth/status"),
    registerBegin: (enrollmentToken?: string) =>
      post<{ options: PublicKeyCredentialCreationOptionsJSON }>(
        "/api/auth/register/begin",
        { enrollment_token: enrollmentToken },
      ),
    registerFinish: (credential: unknown, nickname: string) =>
      post<{ registered: boolean; recovery_codes?: string[] }>(
        "/api/auth/register/finish",
        { credential, nickname },
      ),
    loginBegin: () =>
      post<{ options: PublicKeyCredentialRequestOptionsJSON }>(
        "/api/auth/login/begin",
      ),
    loginFinish: (credential: unknown) =>
      post<{ authenticated: boolean }>("/api/auth/login/finish", { credential }),
    recover: (code: string) =>
      post<{ authenticated: boolean; recovery_codes_left: number }>(
        "/api/auth/recover",
        { code },
      ),
    regenerateRecoveryCodes: () =>
      post<{ recovery_codes: string[] }>("/api/auth/recovery-codes/regenerate"),
    passkeys: () =>
      get<
        {
          id: number;
          nickname: string;
          created_at: string;
          last_used_at: string | null;
        }[]
      >("/api/auth/passkeys"),
    deletePasskey: (id: number) =>
      del<{ deleted: boolean; remaining: number }>(`/api/auth/passkeys/${id}`),
    logout: () => post<{ authenticated: boolean }>("/api/auth/logout"),
  },

  trips: {
    list: () => get<TripSummary[]>("/api/trips"),
    get: (id: number) => get<TripDetail>(`/api/trips/${id}`),
    create: (body: { notes?: string } = {}) =>
      post<TripDetail>("/api/trips", body),
    update: (id: number, body: Record<string, unknown>) =>
      patch<TripDetail>(`/api/trips/${id}`, body),
    remove: (id: number) => del<void>(`/api/trips/${id}`),
    /** Fold `otherId` into `id`; `id` survives, `otherId` is deleted. */
    merge: (id: number, otherId: number) =>
      post<TripDetail>(`/api/trips/${id}/merge`, { other_trip_id: otherId }),
    /** Permanently dismiss the merge suggestion between the two trips. */
    keepSeparate: (id: number, otherId: number) =>
      post<TripDetail>(`/api/trips/${id}/keep-separate`, {
        other_trip_id: otherId,
      }),

    addStay: (tripId: number, body: Record<string, unknown>) =>
      post<TripDetail>(`/api/trips/${tripId}/stays`, body),
    updateStay: (tripId: number, stayId: number, body: Record<string, unknown>) =>
      patch<TripDetail>(`/api/trips/${tripId}/stays/${stayId}`, body),
    removeStay: (tripId: number, stayId: number) =>
      del<TripDetail>(`/api/trips/${tripId}/stays/${stayId}`),

    addLeg: (tripId: number, body: Record<string, unknown>) =>
      post<TripDetail>(`/api/trips/${tripId}/legs`, body),
    updateLeg: (tripId: number, legId: number, body: Record<string, unknown>) =>
      patch<TripDetail>(`/api/trips/${tripId}/legs/${legId}`, body),
    removeLeg: (tripId: number, legId: number) =>
      del<TripDetail>(`/api/trips/${tripId}/legs/${legId}`),

    addRequirement: (tripId: number, body: Record<string, unknown>) =>
      post<TripDetail>(`/api/trips/${tripId}/requirements`, body),
    updateRequirement: (
      tripId: number,
      reqId: number,
      body: Record<string, unknown>,
    ) => patch<TripDetail>(`/api/trips/${tripId}/requirements/${reqId}`, body),
    removeRequirement: (tripId: number, reqId: number) =>
      del<TripDetail>(`/api/trips/${tripId}/requirements/${reqId}`),

    updateEntry: (tripId: number, entryId: number, body: Record<string, unknown>) =>
      patch<TripDetail>(`/api/trips/${tripId}/entries/${entryId}`, body),
  },

  passports: {
    list: () => get<Passport[]>("/api/passports"),
    create: (body: Record<string, unknown>) =>
      post<Passport>("/api/passports", body),
    update: (id: number, body: Record<string, unknown>) =>
      patch<Passport>(`/api/passports/${id}`, body),
    remove: (id: number) => del<void>(`/api/passports/${id}`),
    history: (countryCode: string) =>
      get<(CountryEntryHistory)[]>(`/api/passports/history/${countryCode}`),
  },

  geo: {
    cities: (country: string, q: string) =>
      get<CitySuggestion[]>(
        `/api/geo/cities?country=${encodeURIComponent(country)}&q=${encodeURIComponent(q)}`,
      ),
  },

  notes: {
    list: (params: Record<string, string> = {}) => {
      const qs = new URLSearchParams(params).toString();
      return get<Note[]>(`/api/notes${qs ? `?${qs}` : ""}`);
    },
    create: (body: Record<string, unknown>) => post<Note>("/api/notes", body),
    update: (id: number, body: Record<string, unknown>) =>
      patch<Note>(`/api/notes/${id}`, body),
    remove: (id: number) => del<void>(`/api/notes/${id}`),
  },

  review: {
    list: () => get<ReviewItem[]>("/api/review"),
    count: () => get<{ pending: number }>("/api/review/count"),
    accept: (id: number, overrides: Record<string, unknown> = {}) =>
      post<{
        accepted: boolean;
        trip_id: number;
        created_new_trip: boolean;
        stay_id: number | null;
        leg_id: number | null;
      }>(`/api/review/${id}/accept`, overrides),
    reject: (id: number) =>
      post<{ rejected: boolean }>(`/api/review/${id}/reject`),
    poll: () =>
      post<{
        polled: boolean;
        ingest: { ingested: number; baselined?: boolean };
        extraction: { processed: number; proposed: number };
      }>("/api/review/poll"),
  },
};

export interface CitySuggestion {
  name: string;
  lat: number;
  lon: number;
}

export interface CountryEntryHistory {
  id: number;
  country_code: string;
  entered_on: string;
  port_of_entry: string;
  trip_label: string | null;
  passport_nationality: string | null;
}

// Minimal shapes for the WebAuthn JSON the server hands back. The DOM lib's
// own types describe the ArrayBuffer form, not the base64url JSON form.
export interface PublicKeyCredentialCreationOptionsJSON {
  rp: { id: string; name: string };
  user: { id: string; name: string; displayName: string };
  challenge: string;
  pubKeyCredParams: { type: "public-key"; alg: number }[];
  timeout?: number;
  excludeCredentials?: { id: string; type: "public-key"; transports?: string[] }[];
  authenticatorSelection?: Record<string, unknown>;
  attestation?: string;
}

export interface PublicKeyCredentialRequestOptionsJSON {
  challenge: string;
  timeout?: number;
  rpId: string;
  allowCredentials?: { id: string; type: "public-key"; transports?: string[] }[];
  userVerification?: string;
}
