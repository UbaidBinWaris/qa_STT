"use client";

import {
  api,
  ApiError,
  type AdminBot,
  type AdminPayment,
  type AdminStats,
  type AdminUser,
  type SessionUser,
} from "@ascras/api-client";
import { ThemeToggle } from "@ascras/ui";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

type Tab = "overview" | "users" | "bots" | "payments";

export default function AdminHome() {
  const router = useRouter();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [bots, setBots] = useState<AdminBot[]>([]);
  const [payments, setPayments] = useState<AdminPayment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [s, u, b, p] = await Promise.all([
      api.admin.stats(),
      api.admin.users(),
      api.admin.bots(),
      api.admin.payments(),
    ]);
    setStats(s);
    setUsers(u);
    setBots(b);
    setPayments(p);
  }, []);

  useEffect(() => {
    api
      .me()
      .then(({ user }) => {
        // A signed-in client must not see an admin shell at all, even briefly.
        if (user.role !== "ADMIN") {
          router.replace("/login");
          return;
        }
        setUser(user);
        return refresh();
      })
      .catch(() => router.replace("/login"));
  }, [refresh, router]);

  async function run(action: () => Promise<unknown>, message: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
      setNotice(message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  }

  if (!user) return <main className="ascras-shell">Loading…</main>;

  return (
    <main className="ascras-shell">
      <nav className="ascras-nav">
        <div className="ascras-brand">
          <span className="ascras-mark">A</span>
          <span>ASCRAS Admin</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="ascras-muted">{user.name}</span>
          <ThemeToggle />
          <button
            className="ascras-btn ascras-btn-ghost"
            onClick={async () => {
              await api.logout();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </nav>

      <div className="tabs" style={{ marginBottom: 16 }}>
        {(["overview", "users", "bots", "payments"] as Tab[]).map((t) => (
          <button
            key={t}
            className={`ascras-btn ${tab === t ? "" : "ascras-btn-ghost"}`}
            onClick={() => setTab(t)}
          >
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {notice && <p className="ascras-muted" style={{ marginBottom: 10 }}>{notice}</p>}
      {error && <p className="ascras-error" style={{ marginBottom: 10 }}>{error}</p>}

      {tab === "overview" && stats && <Overview stats={stats} />}
      {tab === "users" && <Users users={users} run={run} />}
      {tab === "bots" && <Bots bots={bots} users={users} run={run} />}
      {tab === "payments" && <Payments payments={payments} users={users} run={run} />}
    </main>
  );
}

function Overview({ stats }: { stats: AdminStats }) {
  return (
    <section className="stat-grid">
      <Card label="Users" value={stats.users} />
      <Card label="Bots" value={stats.bots} />
      <Card label="Calls" value={stats.calls} />
      <Card label="Completed" value={stats.completed} />
      <Card label="Failed" value={stats.failed} />
      <Card label="Recorded revenue" value={`$${stats.revenue.toFixed(2)}`} />
    </section>
  );
}

function Card({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="ascras-card stat">
      <span className="stat-value">{String(value)}</span>
      <span className="ascras-muted" style={{ fontSize: 12 }}>{label}</span>
    </div>
  );
}

function Users({
  users,
  run,
}: {
  users: AdminUser[];
  run: (a: () => Promise<unknown>, m: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    email: "", name: "", password: "", contactChannel: "whatsapp", contactHandle: "",
  });

  return (
    <>
      <section className="ascras-card" style={{ marginBottom: 16 }}>
        <h2 className="section-h">Create an account</h2>
        <p className="ascras-muted" style={{ marginTop: 0, fontSize: 13 }}>
          There is no self-serve signup. Create the account here, then send the
          credentials over the channel the client prefers.
        </p>
        <div className="form-grid">
          <Field label="Email">
            <input className="ascras-input" value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label="Name">
            <input className="ascras-input" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Password">
            <input className="ascras-input" value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <Field label="Contact via">
            <select className="ascras-input" value={form.contactChannel}
              onChange={(e) => setForm({ ...form, contactChannel: e.target.value })}>
              <option value="whatsapp">WhatsApp</option>
              <option value="discord">Discord</option>
              <option value="telegram">Telegram</option>
            </select>
          </Field>
          <Field label="Handle / number">
            <input className="ascras-input" value={form.contactHandle}
              onChange={(e) => setForm({ ...form, contactHandle: e.target.value })} />
          </Field>
        </div>
        <button
          className="ascras-btn"
          style={{ marginTop: 12 }}
          onClick={() =>
            run(() => api.admin.createUser(form), `Account created for ${form.email}`)
          }
        >
          Create account
        </button>
      </section>

      <section className="ascras-card">
        <h2 className="section-h">Accounts</h2>
        <table className="ascras-table">
          <thead>
            <tr>
              <th>Name</th><th>Email</th><th>Role</th><th>Status</th>
              <th>Contact</th><th>Calls</th><th>Paid</th><th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.name}</td>
                <td className="ascras-muted">{u.email}</td>
                <td>{u.role}</td>
                <td>
                  <span className={`ascras-chip ${u.status === "ACTIVE" ? "chip-verified" : "chip-conflict"}`}>
                    {u.status.toLowerCase()}
                  </span>
                </td>
                <td className="ascras-muted">
                  {u.contactChannel ? `${u.contactChannel}: ${u.contactHandle ?? "—"}` : "—"}
                </td>
                <td>{u.calls}</td>
                <td>${u.paidTotal.toFixed(2)}</td>
                <td>
                  <button
                    className="ascras-btn ascras-btn-ghost"
                    onClick={() =>
                      run(
                        () =>
                          api.admin.updateUser(u.id, {
                            status: u.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE",
                          }),
                        u.status === "ACTIVE"
                          ? `${u.email} suspended — their sessions were ended immediately`
                          : `${u.email} reactivated`,
                      )
                    }
                  >
                    {u.status === "ACTIVE" ? "Suspend" : "Activate"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Bots({
  bots, users, run,
}: {
  bots: AdminBot[];
  users: AdminUser[];
  run: (a: () => Promise<unknown>, m: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    ownerId: "", name: "", dailyCallLimit: "", dailyMinuteLimit: "",
    windowStart: "", windowEnd: "", timezone: "Asia/Karachi",
  });

  const toMinutes = (hhmm: string) => {
    if (!hhmm) return null;
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + (m || 0);
  };
  const fromMinutes = (m: number | null) =>
    m == null ? "—" : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

  return (
    <>
      <section className="ascras-card" style={{ marginBottom: 16 }}>
        <h2 className="section-h">Create a bot</h2>
        <p className="ascras-muted" style={{ marginTop: 0, fontSize: 13 }}>
          Limits are per day in the bot&apos;s own timezone. Leave a limit blank for
          unlimited. A time window confines processing to those hours.
        </p>
        <div className="form-grid">
          <Field label="Owner">
            <select className="ascras-input" value={form.ownerId}
              onChange={(e) => setForm({ ...form, ownerId: e.target.value })}>
              <option value="">Select a client…</option>
              {users.filter((u) => u.role === "CLIENT").map((u) => (
                <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
              ))}
            </select>
          </Field>
          <Field label="Bot name">
            <input className="ascras-input" value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Calls / day">
            <input className="ascras-input" type="number" placeholder="unlimited"
              value={form.dailyCallLimit}
              onChange={(e) => setForm({ ...form, dailyCallLimit: e.target.value })} />
          </Field>
          <Field label="Minutes / day">
            <input className="ascras-input" type="number" placeholder="unlimited"
              value={form.dailyMinuteLimit}
              onChange={(e) => setForm({ ...form, dailyMinuteLimit: e.target.value })} />
          </Field>
          <Field label="Window start">
            <input className="ascras-input" type="time" value={form.windowStart}
              onChange={(e) => setForm({ ...form, windowStart: e.target.value })} />
          </Field>
          <Field label="Window end">
            <input className="ascras-input" type="time" value={form.windowEnd}
              onChange={(e) => setForm({ ...form, windowEnd: e.target.value })} />
          </Field>
          <Field label="Timezone">
            <input className="ascras-input" value={form.timezone}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
          </Field>
        </div>
        <button
          className="ascras-btn"
          style={{ marginTop: 12 }}
          onClick={() =>
            run(
              () =>
                api.admin.createBot({
                  ownerId: form.ownerId,
                  name: form.name,
                  status: "ENABLED",
                  dailyCallLimit: form.dailyCallLimit ? Number(form.dailyCallLimit) : null,
                  dailyMinuteLimit: form.dailyMinuteLimit ? Number(form.dailyMinuteLimit) : null,
                  windowStartMinute: toMinutes(form.windowStart),
                  windowEndMinute: toMinutes(form.windowEnd),
                  timezone: form.timezone,
                }),
              `Bot "${form.name}" created and enabled`,
            )
          }
        >
          Create bot
        </button>
      </section>

      <section className="ascras-card">
        <h2 className="section-h">Bots</h2>
        <table className="ascras-table">
          <thead>
            <tr>
              <th>Bot</th><th>Owner</th><th>Status</th>
              <th>Calls/day</th><th>Mins/day</th><th>Window</th><th>Processed</th><th />
            </tr>
          </thead>
          <tbody>
            {bots.map((b) => (
              <tr key={b.id}>
                <td>{b.name}</td>
                <td className="ascras-muted">{b.owner?.email ?? b.ownerId}</td>
                <td>
                  <span className={`ascras-chip ${b.status === "ENABLED" ? "chip-verified" : "chip-uncertain"}`}>
                    {b.status.toLowerCase()}
                  </span>
                </td>
                <td>{b.dailyCallLimit ?? "∞"}</td>
                <td>{b.dailyMinuteLimit ?? "∞"}</td>
                <td className="ascras-muted">
                  {b.windowStartMinute == null
                    ? "any time"
                    : `${fromMinutes(b.windowStartMinute)}–${fromMinutes(b.windowEndMinute)} ${b.timezone}`}
                </td>
                <td>{b._count?.calls ?? 0}</td>
                <td>
                  <button
                    className="ascras-btn ascras-btn-ghost"
                    onClick={() =>
                      run(
                        () =>
                          api.admin.updateBot(b.id, {
                            status: b.status === "ENABLED" ? "DISABLED" : "ENABLED",
                          }),
                        `${b.name} ${b.status === "ENABLED" ? "disabled" : "enabled"}`,
                      )
                    }
                  >
                    {b.status === "ENABLED" ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Payments({
  payments, users, run,
}: {
  payments: AdminPayment[];
  users: AdminUser[];
  run: (a: () => Promise<unknown>, m: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    userId: "", amount: "", currency: "USD", method: "bank transfer", reference: "", note: "",
  });

  return (
    <>
      <section className="ascras-card" style={{ marginBottom: 16 }}>
        <h2 className="section-h">Record a payment</h2>
        <p className="ascras-muted" style={{ marginTop: 0, fontSize: 13 }}>
          This is a ledger of money that changed hands elsewhere. Nothing here
          charges anyone or talks to a payment processor.
        </p>
        <div className="form-grid">
          <Field label="Client">
            <select className="ascras-input" value={form.userId}
              onChange={(e) => setForm({ ...form, userId: e.target.value })}>
              <option value="">Select a client…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
              ))}
            </select>
          </Field>
          <Field label="Amount">
            <input className="ascras-input" type="number" step="0.01" value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          </Field>
          <Field label="Currency">
            <input className="ascras-input" value={form.currency}
              onChange={(e) => setForm({ ...form, currency: e.target.value })} />
          </Field>
          <Field label="Method">
            <input className="ascras-input" value={form.method}
              onChange={(e) => setForm({ ...form, method: e.target.value })} />
          </Field>
          <Field label="Reference">
            <input className="ascras-input" value={form.reference}
              onChange={(e) => setForm({ ...form, reference: e.target.value })} />
          </Field>
          <Field label="Note">
            <input className="ascras-input" value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
        </div>
        <button
          className="ascras-btn"
          style={{ marginTop: 12 }}
          onClick={() =>
            run(
              () =>
                api.admin.recordPayment({
                  userId: form.userId,
                  amount: Number(form.amount),
                  currency: form.currency,
                  method: form.method,
                  reference: form.reference || undefined,
                  note: form.note || undefined,
                }),
              "Payment recorded",
            )
          }
        >
          Record payment
        </button>
      </section>

      <section className="ascras-card">
        <h2 className="section-h">Ledger</h2>
        <table className="ascras-table">
          <thead>
            <tr><th>Date</th><th>Client</th><th>Amount</th><th>Method</th><th>Reference</th><th>Note</th></tr>
          </thead>
          <tbody>
            {payments.map((p) => (
              <tr key={p.id}>
                <td className="ascras-muted">{new Date(p.createdAt).toLocaleDateString()}</td>
                <td>{p.user?.name ?? p.userId}</td>
                <td><strong>{p.currency} {Number(p.amount).toFixed(2)}</strong></td>
                <td className="ascras-muted">{p.method ?? "—"}</td>
                <td className="ascras-muted">{p.reference ?? "—"}</td>
                <td className="ascras-muted">{p.note ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="ascras-label">{label}</label>
      {children}
    </div>
  );
}
