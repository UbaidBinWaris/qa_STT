/**
 * Typed access to the ASCRAS backend, shared by portal and admin.
 *
 * Every request sends credentials: authentication is a httpOnly session cookie,
 * which the browser will not attach cross-origin unless asked. In development
 * the apps run on different ports to the API, so this is not optional.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5003";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      ...(init.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(init.headers ?? {}),
    },
  });

  if (!res.ok) {
    const detail = await res
      .json()
      .then((d) => d.message ?? d.detail)
      .catch(() => null);
    throw new ApiError(detail ?? res.statusText, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  base: BASE,

  login: (email: string, password: string) =>
    request<{ user: SessionUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ user: SessionUser }>("/api/auth/me"),
  health: () => request<HealthReport>("/api/health"),

  // client
  myBots: () => request<BotWithQuota[]>("/api/bots"),
  myCalls: () => request<CallSummary[]>("/api/calls"),
  call: (id: string) => request<CallDetail>(`/api/calls/${id}`),
  audioUrl: (id: string) => request<{ url: string }>(`/api/calls/${id}/audio-url`),
  deleteCall: (id: string) => request<{ deleted: string }>(`/api/calls/${id}`, { method: "DELETE" }),
  upload: (file: File, botId: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("botId", botId);
    return request<UploadResult>("/api/calls", { method: "POST", body: form });
  },

  // admin
  admin: {
    stats: () => request<AdminStats>("/api/admin/stats"),
    users: () => request<AdminUser[]>("/api/admin/users"),
    createUser: (body: NewUser) =>
      request<SessionUser>("/api/admin/users", { method: "POST", body: JSON.stringify(body) }),
    updateUser: (id: string, body: Partial<{ status: string; name: string; password: string }>) =>
      request<SessionUser>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    bots: () => request<AdminBot[]>("/api/admin/bots"),
    createBot: (body: NewBot) =>
      request<AdminBot>("/api/admin/bots", { method: "POST", body: JSON.stringify(body) }),
    updateBot: (id: string, body: Partial<NewBot>) =>
      request<AdminBot>(`/api/admin/bots/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    botUsage: (id: string) => request<BotUsage[]>(`/api/admin/bots/${id}/usage`),
    payments: () => request<AdminPayment[]>("/api/admin/payments"),
    recordPayment: (body: NewPayment) =>
      request<AdminPayment>("/api/admin/payments", { method: "POST", body: JSON.stringify(body) }),
    audit: () => request<AuditEntry[]>("/api/admin/audit"),
  },
};

export interface SessionUser {
  id: string; email: string; name: string; role: "ADMIN" | "CLIENT"; status?: string;
}
export interface HealthReport {
  status: string; database: boolean; worker: boolean; workerDetail?: unknown;
}
export interface Quota {
  allowed: boolean; reason?: string;
  callsRemaining?: number | null; minutesRemaining?: number | null;
}
export interface BotWithQuota {
  id: string; name: string; status: "ENABLED" | "DISABLED";
  dailyCallLimit: number | null; dailyMinuteLimit: number | null;
  windowStartMinute: number | null; windowEndMinute: number | null;
  timezone: string; canTranscribe: boolean; canRunQa: boolean; canExport: boolean;
  quota: Quota;
}
export interface CallSummary {
  id: string; filename: string; durationSeconds: number | null;
  status: "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";
  stage: string | null; progress: number; score: number | null;
  reliabilityScore: number | null; createdAt: string; error: string | null;
}
export interface CallDetail extends CallSummary {
  transcript: Segment[] | null;
  metrics: Record<string, any> | null;
  qa: Record<string, any> | null;
  reliability: Record<string, any> | null;
  prosody: Record<string, any> | null;
}
export interface Segment {
  role: string; start: number; end: number; text: string;
  confidence: number | null; uncertain: boolean; crosstalk?: boolean;
  prosody?: { tone?: string; arousal?: number | null } | null;
  words: Word[];
}
export interface Word {
  word: string; start: number; end: number; confidence: number | null;
  uncertain: boolean; verdict?: string | null; recovered?: boolean; risk: string[];
}
export interface UploadResult {
  callId: string; status: string; duplicate: boolean; filename: string;
}
export interface AdminStats {
  users: number; bots: number; calls: number; completed: number; failed: number; revenue: number;
}
export interface AdminUser {
  id: string; email: string; name: string; role: string; status: string;
  contactChannel: string | null; contactHandle: string | null;
  calls: number; bots: number; paidTotal: number; createdAt: string;
}
export interface NewUser {
  email: string; name: string; password: string;
  role?: "ADMIN" | "CLIENT"; contactChannel?: string; contactHandle?: string;
}
export interface AdminBot {
  id: string; name: string; status: "ENABLED" | "DISABLED"; ownerId: string;
  dailyCallLimit: number | null; dailyMinuteLimit: number | null;
  windowStartMinute: number | null; windowEndMinute: number | null;
  timezone: string; canTranscribe: boolean; canRunQa: boolean; canExport: boolean;
  owner?: { id: string; email: string; name: string };
  _count?: { calls: number };
}
export interface NewBot {
  ownerId: string; name: string; status?: "ENABLED" | "DISABLED";
  dailyCallLimit?: number | null; dailyMinuteLimit?: number | null;
  windowStartMinute?: number | null; windowEndMinute?: number | null;
  timezone?: string; canTranscribe?: boolean; canRunQa?: boolean; canExport?: boolean;
}
export interface BotUsage {
  id: string; day: string; callsProcessed: number; minutesProcessed: number;
}
export interface AdminPayment {
  id: string; userId: string; amount: string; currency: string;
  method: string | null; reference: string | null; note: string | null;
  createdAt: string; user?: { id: string; email: string; name: string };
}
export interface NewPayment {
  userId: string; amount: number; currency?: string;
  method?: string; reference?: string; note?: string;
}
export interface AuditEntry {
  id: string; actorId: string | null; action: string; target: string | null;
  ip: string | null; createdAt: string;
}
