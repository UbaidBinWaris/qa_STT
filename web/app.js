const $ = (id) => document.getElementById(id);
const API = "";

let currentCall = null;
let currentData = null;
let pollTimer = null;

const fmtTime = (s) => {
    if (s == null) return "–";
    const m = Math.floor(s / 60), sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
};

// ngrok's free tier serves an interstitial HTML page to browser-like requests,
// which would arrive here instead of JSON. This header opts out of it.
const BASE_HEADERS = { "ngrok-skip-browser-warning": "true" };

async function api(path, opts = {}) {
    const res = await fetch(API + path, {
        ...opts,
        headers: { ...BASE_HEADERS, ...(opts.headers || {}) },
    });
    if (res.status === 401) {
        window.location.href = "/login.html";
        throw new Error("Authentication required");
    }
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
}

async function setupAuth() {
    const { auth_required } = await api("/api/auth-status");
    if (!auth_required) return;
    const btn = document.createElement("button");
    btn.className = "tab";
    btn.textContent = "Sign out";
    btn.onclick = async () => {
        await api("/api/logout", { method: "POST" });
        window.location.href = "/login.html";
    };
    document.getElementById("statusIndicator").appendChild(btn);
}

/* ---------- health ---------- */

async function refreshHealth() {
    try {
        const h = await api("/api/health");
        $("statusDot").className = "status-dot green";
        $("statusText").textContent =
            `${h.gpu || h.device} · ${h.vram_used_gb} GB · queue ${h.queue_depth}`;
    } catch {
        $("statusDot").className = "status-dot red";
        $("statusText").textContent = "Server offline";
    }
}

/* ---------- call list ---------- */

async function refreshCalls() {
    const calls = await api("/api/calls");
    const list = $("callList");
    list.innerHTML = "";
    for (const c of calls) {
        const el = document.createElement("div");
        el.className = "call-item" + (c.id === currentCall ? " active" : "");
        el.onclick = () => selectCall(c.id);
        el.innerHTML = `
            <div class="call-row">
                <span class="call-name">${escapeHtml(c.filename)}</span>
                ${c.score != null ? `<span class="chip score-${scoreBand(c.score)}">${c.score}</span>` : ""}
            </div>
            <div class="call-sub">
                <span class="status-${c.status}">${c.status === "processing" ? (c.stage || "processing") : c.status}</span>
                <span>${c.duration ? fmtTime(c.duration) : ""}</span>
            </div>`;
        list.appendChild(el);
    }
    return calls;
}

const scoreBand = (s) => (s >= 80 ? "good" : s >= 55 ? "mid" : "bad");

/* ---------- upload ---------- */

function setupUpload() {
    const dz = $("dropzone"), input = $("fileInput");
    dz.onclick = () => input.click();
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
    dz.ondragleave = () => dz.classList.remove("dragover");
    dz.ondrop = (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
        upload(e.dataTransfer.files);
    };
    input.onchange = () => upload(input.files);
}

async function upload(files) {
    for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        try {
            const { call_id } = await api("/api/calls", { method: "POST", body: fd });
            await refreshCalls();
            selectCall(call_id);
        } catch (e) {
            alert(`Upload failed: ${e.message}`);
        }
    }
    $("fileInput").value = "";
}

/* ---------- detail ---------- */

async function selectCall(id) {
    currentCall = id;
    $("emptyState").classList.add("hidden");
    $("detailBody").classList.remove("hidden");
    $("player").src = `/api/calls/${id}/audio`;
    await loadCall();
    await refreshCalls();
    startPolling();
}

async function loadCall() {
    const data = await api(`/api/calls/${currentCall}`);
    currentData = data;

    $("callTitle").textContent = data.filename;
    $("callMeta").textContent = [
        data.duration ? fmtTime(data.duration) : null,
        data.transcript.length ? `${data.transcript.length} turns` : null,
        new Date(data.created_at * 1000).toLocaleString(),
    ].filter(Boolean).join(" · ");

    const done = data.status === "completed";
    $("progressPanel").classList.toggle("hidden", done || data.status === "failed");
    $("progressStage").textContent = data.error ? `failed: ${data.error}` : (data.stage || data.status);
    $("progressPct").textContent = `${data.progress}%`;
    $("progressFill").style.width = `${data.progress}%`;

    const score = data.qa?.score;
    $("scoreBadge").classList.toggle("hidden", score == null);
    if (score != null) {
        $("scoreValue").textContent = score;
        $("scoreBadge").className = `score-badge score-${scoreBand(score)}`;
    }

    renderTranscript(data.transcript);
    renderQA(data.qa);
    renderMetrics(data.metrics);
    return data;
}

function renderTranscript(segments) {
    const panel = $("panel-transcript");
    if (!segments.length) {
        panel.innerHTML = `<p class="muted">Transcript will appear when processing completes.</p>`;
        return;
    }
    panel.innerHTML = "";
    for (const seg of segments) {
        const el = document.createElement("div");
        el.className = `turn role-${seg.role.toLowerCase().replace(/\s+/g, "-")}`;
        el.dataset.start = seg.start;
        el.dataset.end = seg.end;
        const words = seg.words
            .map((w) => `<span class="w" data-start="${w.start}" data-end="${w.end}">${escapeHtml(w.word)}</span>`)
            .join(" ");
        el.innerHTML = `
            <div class="turn-head">
                <span class="speaker">${escapeHtml(seg.role)}</span>
                <span class="ts">${fmtTime(seg.start)}</span>
            </div>
            <div class="turn-text">${words}</div>`;
        el.onclick = () => seek(seg.start);
        panel.appendChild(el);
    }
}

function renderQA(qa) {
    const panel = $("panel-qa");
    if (!qa) {
        panel.innerHTML = `<p class="muted">QA analysis pending.</p>`;
        return;
    }
    const issues = qa.compliance?.issues || [];
    panel.innerHTML = `
        <h3>Summary</h3><p>${escapeHtml(qa.summary || "")}</p>

        <h3>Compliance</h3>
        <div class="pill-row">
            ${pill("Identity verified", qa.compliance?.identity_verified)}
            ${pill("Recording disclosed", qa.compliance?.recording_disclosed)}
        </div>
        ${issues.length
            ? issues.map((i) => `
                <div class="finding severity-${i.severity}" onclick="seek(${i.timestamp})">
                    <div class="finding-head"><strong>${escapeHtml(i.rule)}</strong>
                        <span class="chip">${i.severity}</span>
                        <span class="ts">${fmtTime(i.timestamp)}</span></div>
                    <blockquote>${escapeHtml(i.quote)}</blockquote>
                </div>`).join("")
            : `<p class="muted">No compliance issues detected.</p>`}

        <h3>Sentiment &amp; Intent</h3>
        <div class="pill-row">
            <span class="chip">Customer: ${escapeHtml(qa.sentiment?.customer || "–")}</span>
            <span class="chip">Agent: ${escapeHtml(qa.sentiment?.agent || "–")}</span>
            <span class="chip">Trend: ${escapeHtml(qa.sentiment?.trajectory || "–")}</span>
            <span class="chip">Buying intent: ${escapeHtml(qa.buying_intent?.level || "–")}</span>
        </div>
        ${(qa.buying_intent?.evidence || []).map((e) => `<blockquote>${escapeHtml(e)}</blockquote>`).join("")}

        <h3>Objections</h3>
        ${(qa.objections || []).length
            ? qa.objections.map((o) => `
                <div class="finding" onclick="seek(${o.timestamp})">
                    <div class="finding-head"><strong>${escapeHtml(o.type)}</strong>
                        <span class="chip ${o.handled ? "score-good" : "score-bad"}">${o.handled ? "handled" : "unhandled"}</span>
                        <span class="ts">${fmtTime(o.timestamp)}</span></div>
                    <blockquote>${escapeHtml(o.quote)}</blockquote>
                </div>`).join("")
            : `<p class="muted">None raised.</p>`}

        <h3>Coaching</h3>
        <ul>${(qa.coaching_feedback || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>

        <h3>Action Items</h3>
        <ul>${(qa.action_items || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>

        <h3>CRM Notes</h3><p>${escapeHtml(qa.crm_notes || "")}</p>

        <h3>Follow-up Email</h3><pre>${escapeHtml(qa.followup_email || "")}</pre>`;
}

const pill = (label, ok) =>
    `<span class="chip ${ok ? "score-good" : "score-bad"}">${label}: ${ok ? "yes" : "no"}</span>`;

function renderMetrics(m) {
    const panel = $("panel-metrics");
    if (!m) {
        panel.innerHTML = `<p class="muted">Metrics pending.</p>`;
        return;
    }
    const ratio = m.talk_ratio || {};
    panel.innerHTML = `
        <h3>Talk Ratio</h3>
        <div class="bar">
            <div class="bar-agent" style="width:${ratio.Agent || 0}%">${(ratio.Agent || 0).toFixed(0)}% Agent</div>
            <div class="bar-cust" style="width:${ratio.Customer || 0}%">${(ratio.Customer || 0).toFixed(0)}% Customer</div>
        </div>
        <div class="stat-grid">
            ${stat("Duration", fmtTime(m.duration))}
            ${stat("Turns", m.turns)}
            ${stat("Agent WPM", m.wpm?.Agent)}
            ${stat("Customer WPM", m.wpm?.Customer)}
            ${stat("Silence", `${m.silence_total}s`)}
            ${stat("Longest pause", `${m.longest_silence}s`)}
            ${stat("Agent interrupts", m.interruptions?.Agent || 0)}
            ${stat("Customer interrupts", m.interruptions?.Customer || 0)}
        </div>
        <h3>Dead Air</h3>
        ${(m.dead_air_events || []).length
            ? `<div class="pill-row">${m.dead_air_events.map((d) =>
                `<span class="chip clickable" onclick="seek(${d.start})">${fmtTime(d.start)} · ${d.duration}s</span>`).join("")}</div>`
            : `<p class="muted">No pauses over 3s.</p>`}`;
}

const stat = (label, value) =>
    `<div class="stat"><span class="stat-value">${value ?? "–"}</span><span class="stat-label">${label}</span></div>`;

/* ---------- audio sync ---------- */

function seek(t) {
    const p = $("player");
    p.currentTime = t;
    p.play();
}
window.seek = seek;

function setupPlayerSync() {
    const p = $("player");
    p.ontimeupdate = () => {
        const t = p.currentTime;
        let activeTurn = null;
        for (const turn of document.querySelectorAll(".turn")) {
            const on = t >= +turn.dataset.start && t <= +turn.dataset.end;
            turn.classList.toggle("playing", on);
            if (on) activeTurn = turn;
        }
        document.querySelectorAll(".w.spoken").forEach((w) => w.classList.remove("spoken"));
        if (activeTurn) {
            for (const w of activeTurn.querySelectorAll(".w")) {
                if (t >= +w.dataset.start && t <= +w.dataset.end) w.classList.add("spoken");
            }
        }
    };
}

/* ---------- polling ---------- */

function startPolling() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
        if (!currentCall) return;
        const { status, stage, progress } = await api(`/api/calls/${currentCall}/status`);
        $("progressStage").textContent = stage || status;
        $("progressPct").textContent = `${progress}%`;
        $("progressFill").style.width = `${progress}%`;
        if (status === "completed" || status === "failed") {
            clearInterval(pollTimer);
            await loadCall();
        }
        await refreshCalls();
        await refreshHealth();
    }, 2000);
}

/* ---------- search ---------- */

function setupSearch() {
    let t;
    $("searchInput").oninput = (e) => {
        clearTimeout(t);
        t = setTimeout(() => runSearch(e.target.value.trim()), 300);
    };
}

async function runSearch(q) {
    if (!q) return refreshCalls();
    const results = await api(`/api/search?q=${encodeURIComponent(q)}`);
    const list = $("callList");
    list.innerHTML = results.length ? "" : `<p class="muted">No matches.</p>`;
    for (const r of results) {
        const el = document.createElement("div");
        el.className = "call-item";
        el.onclick = async () => { await selectCall(r.call_id); seek(r.start); };
        el.innerHTML = `
            <div class="call-row"><span class="call-name">${escapeHtml(r.filename)}</span>
            <span class="ts">${fmtTime(r.start)}</span></div>
            <div class="snippet">${highlightSnippet(r.snippet)}</div>`;
        list.appendChild(el);
    }
}

/* ---------- tabs ---------- */

function setupTabs() {
    for (const tab of document.querySelectorAll(".tab")) {
        tab.onclick = () => {
            document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            for (const name of ["transcript", "qa", "metrics"]) {
                $(`panel-${name}`).classList.toggle("hidden", name !== tab.dataset.tab);
            }
        };
    }
}

// FTS5 marks hits with control chars so the text can be escaped before
// the <mark> tags go in.
const highlightSnippet = (s) =>
    escapeHtml(s).replaceAll("\u0001", "<mark>").replaceAll("\u0002", "</mark>");

function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML;
}

setupUpload();
setupTabs();
setupSearch();
setupPlayerSync();
setupAuth();
refreshHealth();
refreshCalls();
setInterval(refreshHealth, 10000);
