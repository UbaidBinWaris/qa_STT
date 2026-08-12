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
    // Not class "tab": setupTabs() binds panel-switching to every .tab, and the
    // header button sits before the tab bar, so it would also become .tab[0].
    btn.className = "btn-signout";
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

let limits = null;

async function loadLimits() {
    try {
        limits = await api("/api/limits");
    } catch {
        limits = null;  // server-side checks still apply
    }
}

// Cheap client-side screening so obvious rejects cost no upload bandwidth.
// The server re-checks everything; this is convenience, not enforcement.
function preCheck(file) {
    if (!limits) return null;
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (file.name.indexOf(".") === -1 || !limits.allowed_extensions.includes(ext)) {
        return `${file.name}: unsupported format. Accepted: ${limits.allowed_extensions.join(", ")}`;
    }
    if (file.size > limits.max_bytes) {
        return `${file.name}: ${(file.size / 1048576).toFixed(0)} MB exceeds the ${limits.max_upload_mb} MB limit`;
    }
    if (file.size === 0) return `${file.name}: file is empty`;
    return null;
}

async function upload(files) {
    for (const file of files) {
        const problem = preCheck(file);
        if (problem) {
            showUploadError(problem);
            continue;
        }
        const fd = new FormData();
        fd.append("file", file);
        setUploadStatus(`Uploading ${file.name}…`);
        try {
            const res = await api("/api/calls", { method: "POST", body: fd });
            await refreshCalls();
            selectCall(res.call_id);
            setUploadStatus(res.duplicate
                ? `${file.name} was already uploaded — opening the existing call.`
                : "");
        } catch (e) {
            showUploadError(`${file.name}: ${e.message}`);
        }
    }
    $("fileInput").value = "";
}

function setUploadStatus(text) {
    const el = $("uploadStatus");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("hidden", !text);
    el.classList.remove("upload-error");
}

function showUploadError(text) {
    const el = $("uploadStatus");
    if (!el) { alert(text); return; }
    el.textContent = text;
    el.classList.remove("hidden");
    el.classList.add("upload-error");
}

/* ---------- detail ---------- */

async function selectCall(id) {
    currentCall = id;
    $("emptyState").classList.add("hidden");
    $("detailBody").classList.remove("hidden");
    $("player").src = `/api/calls/${id}/audio`;
    await loadCall();
    await fetchAndRenderWaveform(id);
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

    renderReliability(data.reliability);
    renderTranscript(data.transcript, data.reliability?.recovery?.crosstalk_regions || []);
    renderQA(data.qa);
    renderMetrics(data.metrics);
    return data;
}

function renderReliability(rel) {
    const el = $("reliabilityBadge");
    if (!el) return;
    if (!rel || !rel.available || rel.score == null) {
        el.classList.add("hidden");
        return;
    }
    const v = rel.verification || {};
    const conflicts = rel.conflicts || 0;
    const band = conflicts ? "bad" : rel.score >= 95 ? "good" : rel.score >= 88 ? "mid" : "bad";
    el.className = `chip reliability-${band}`;
    el.textContent = conflicts
        ? `Transcript ${rel.score}% · ${conflicts} disputed`
        : `Transcript ${rel.score}%`;
    const rec = rel.recovery || {};
    el.title = `${rel.flagged} of ${rel.total} words uncertain · `
        + `mean confidence ${(rel.mean_confidence * 100).toFixed(1)}%`
        + (v.checked ? ` · second pass: ${v.confirmed} confirmed, ${v.conflict} conflicting `
                       + `(${v.seconds}s)` : "")
        + (rec.recovered ? ` · recovered ${rec.recovered_words} word(s) from `
                           + `${rec.recovered} dropped turn(s)` : "")
        + (rec.crosstalk_seconds ? ` · ${rec.crosstalk_seconds}s cross-talk` : "");
    el.classList.remove("hidden");
}

function renderTranscript(segments, crosstalk = []) {
    const panel = $("panel-transcript");
    if (!segments.length) {
        panel.innerHTML = `<p class="muted">Transcript will appear when processing completes.</p>`;
        return;
    }
    panel.innerHTML = "";

    // Cross-talk that produced no words leaves a hole in the transcript. Rendering
    // the hole is the point: silence here means "we could not hear it", not
    // "nobody spoke", and a customer objection lost under the agent's voice must
    // not simply vanish from the record.
    const gaps = crosstalk.filter((c) =>
        !segments.some((s) => s.start < c.end && s.end > c.start));

    const renderGap = (c) => {
        const el = document.createElement("div");
        el.className = "turn turn-crosstalk";
        el.dataset.start = num(c.start);
        el.dataset.seek = num(c.start);
        el.innerHTML = `
            <div class="turn-head">
                <span class="speaker">Cross-talk</span>
                <span class="ts">${fmtTime(num(c.start))}</span>
            </div>
            <div class="turn-text">\u26a0 Both speakers talking at once for
                ${(num(c.end) - num(c.start)).toFixed(1)}s — speech here could not be
                transcribed. Listen to the audio.</div>`;
        panel.appendChild(el);
    };

    for (const seg of segments) {
        while (gaps.length && gaps[0].start < seg.start) renderGap(gaps.shift());
        const el = document.createElement("div");
        el.className = `turn role-${seg.role.toLowerCase().replace(/\s+/g, "-")}`
            + (seg.uncertain ? " turn-uncertain" : "");
        el.dataset.start = seg.start;
        el.dataset.end = seg.end;
        const words = seg.words
            .map((w) => {
                // Show doubt rather than hiding it: a reviewer needs to know which
                // words the recogniser itself was unsure of before trusting a verdict.
                // Three states, not two: a checked-and-confirmed word reads
                // differently from one the two decoders disagreed about.
                const conflict = w.verdict === "conflict";
                const cls = "w" + (conflict ? " w-conflict"
                    : w.recovered ? " w-recovered"
                    : w.uncertain ? " w-uncertain" : "");
                const title = w.recovered
                    ? "Recovered from audio the first pass missed — verify"
                    : w.confidence != null
                    ? (conflict ? "Decoders disagreed here — check the audio · " : "")
                      + `confidence ${(w.confidence * 100).toFixed(1)}%`
                      + (w.risk && w.risk.length ? ` · ${w.risk.join(", ")}` : "")
                    : "";
                return `<span class="${cls}" data-start="${num(w.start)}" data-end="${num(w.end)}"` +
                       ` title="${escapeHtml(title)}">${escapeHtml(w.word)}</span>`;
            })
            .join(" ");
        el.innerHTML = `
            <div class="turn-head">
                <span class="speaker">${escapeHtml(seg.role)}</span>
                ${seg.crosstalk ? '<span class="chip warn-chip" title="Both speakers talking at once \u2014 words may be missing from this turn">\u26a0 cross-talk, may be incomplete</span>' : ""}
                ${seg.uncertain && !seg.crosstalk ? '<span class="chip warn-chip" title="Some words in this turn are uncertain">\u26a0 check audio</span>' : ""}
                <span class="ts">${fmtTime(seg.start)}</span>
            </div>
            <div class="turn-text">${words}</div>`;
        el.onclick = () => seek(seg.start);
        panel.appendChild(el);
    }
    while (gaps.length) renderGap(gaps.shift());
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
                <div class="finding severity-${severityClass(i.severity)}" data-seek="${num(i.timestamp)}">
                    <div class="finding-head"><strong>${escapeHtml(i.rule)}</strong>
                        <span class="chip">${escapeHtml(i.severity)}</span>
                        ${i.verified?.transcript_uncertain ? '<span class="chip warn-chip" title="Evidence sits on words the recogniser was unsure of">\u26a0 verify audio</span>' : ""}
                        <span class="ts">${fmtTime(num(i.timestamp))}</span></div>
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
                <div class="finding" data-seek="${num(o.timestamp)}">
                    <div class="finding-head"><strong>${escapeHtml(o.type)}</strong>
                        <span class="chip ${o.handled ? "score-good" : "score-bad"}">${o.handled ? "handled" : "unhandled"}</span>
                        <span class="ts">${fmtTime(num(o.timestamp))}</span></div>
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
    `<span class="chip ${ok ? "score-good" : "score-bad"}">${escapeHtml(label)}: ${ok ? "yes" : "no"}</span>`;

// QA fields come from a language model, and the model reads a transcript that a
// caller controls by speaking. Treat every value as hostile: numbers are coerced,
// strings escaped, and CSS classes restricted to a known set.
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const severityClass = (s) => (["low", "medium", "high"].includes(s) ? s : "unknown");

function renderMetrics(m) {
    const panel = $("panel-metrics");
    if (!m) {
        panel.innerHTML = `<p class="muted">Metrics pending.</p>`;
        return;
    }
    const ratio = m.talk_ratio || {};
    const agent = num(ratio.Agent), customer = num(ratio.Customer);
    panel.innerHTML = `
        <h3>Talk Ratio</h3>
        <div class="bar">
            <div class="bar-agent">${agent.toFixed(0)}% Agent</div>
            <div class="bar-cust">${customer.toFixed(0)}% Customer</div>
        </div>
        <div class="stat-grid">
            ${stat("Duration", fmtTime(num(m.duration)))}
            ${stat("Turns", num(m.turns))}
            ${stat("Agent WPM", num(m.wpm?.Agent))}
            ${stat("Customer WPM", num(m.wpm?.Customer))}
            ${stat("Silence", `${num(m.silence_total)}s`)}
            ${stat("Longest pause", `${num(m.longest_silence)}s`)}
            ${stat("Agent interrupts", num(m.interruptions?.Agent))}
            ${stat("Customer interrupts", num(m.interruptions?.Customer))}
        </div>
        <h3>Dead Air</h3>
        ${(m.dead_air_events || []).length
            ? `<div class="pill-row">${m.dead_air_events.map((d) =>
                `<span class="chip clickable" data-seek="${num(d.start)}">${fmtTime(num(d.start))} · ${num(d.duration)}s</span>`).join("")}</div>`
            : `<p class="muted">No pauses over 3s.</p>`}`;

    // Widths are applied through the CSSOM rather than a style attribute, which
    // a strict style-src CSP blocks.
    panel.querySelector(".bar-agent").style.width = `${agent}%`;
    panel.querySelector(".bar-cust").style.width = `${customer}%`;
}

const stat = (label, value) =>
    `<div class="stat"><span class="stat-value">${escapeHtml(String(value ?? "–"))}</span>` +
    `<span class="stat-label">${escapeHtml(label)}</span></div>`;

/* ---------- audio sync ---------- */

function seek(t) {
    const p = $("player");
    p.currentTime = num(t);
    p.play();

    // Auto scroll to active turn on manual seek
    setTimeout(() => {
        const activeTurn = document.querySelector(".turn.playing");
        if (activeTurn) {
            activeTurn.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }, 50);
}
window.seek = seek;

// Delegated instead of inline onclick: rendered data never becomes executable
// markup, which also lets the page run under a script-src 'self' CSP.
document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-seek]");
    if (target) seek(target.dataset.seek);
});

/* ---------- Pitch Waveform Soundbar & Design Toggle ---------- */

let waveformData = null;
let currentMode = localStorage.getItem("soundbar_design_mode") || "waveform";
let lastActiveTurn = null;

function initSoundbarPlayer() {
    setupSoundbarToggle();
    setupWaveformControls();
    setupWaveformInteractions();
    applySoundbarMode(currentMode);
}

function setupSoundbarToggle() {
    const btnWf = $("btnModeWaveform");
    const btnStd = $("btnModeStandard");
    if (!btnWf || !btnStd) return;

    btnWf.onclick = () => applySoundbarMode("waveform");
    btnStd.onclick = () => applySoundbarMode("standard");
}

function applySoundbarMode(mode) {
    currentMode = mode;
    localStorage.setItem("soundbar_design_mode", mode);

    const isWf = mode === "waveform";
    const btnWf = $("btnModeWaveform");
    const btnStd = $("btnModeStandard");
    if (btnWf) btnWf.classList.toggle("active", isWf);
    if (btnStd) btnStd.classList.toggle("active", !isWf);

    const wfView = $("waveformPlayerView");
    const stdView = $("standardPlayerView");
    if (wfView) wfView.classList.toggle("hidden", !isWf);
    if (stdView) stdView.classList.toggle("hidden", isWf);
}

async function fetchAndRenderWaveform(callId) {
    const barsContainer = $("waveformBars");
    if (!barsContainer) return;

    renderSkeletonBars(140);

    try {
        const data = await api(`/api/calls/${callId}/waveform`);
        waveformData = data;
        renderPitchBars(data.heights);
    } catch (e) {
        console.warn("Waveform fetch failed:", e);
        const fallbackHeights = Array.from({ length: 140 }, () => 0.15 + Math.random() * 0.7);
        renderPitchBars(fallbackHeights);
    }
}

function renderSkeletonBars(num) {
    const container = $("waveformBars");
    if (!container) return;
    container.innerHTML = "";
    for (let i = 0; i < num; i++) {
        const bar = document.createElement("div");
        bar.className = "wf-bar";
        bar.style.height = `${15 + Math.sin(i * 0.3) * 10}%`;
        container.appendChild(bar);
    }
}

function renderPitchBars(heights) {
    const container = $("waveformBars");
    if (!container) return;
    container.innerHTML = "";
    const p = $("player");
    const pct = p.duration ? p.currentTime / p.duration : 0;
    const activeIdx = Math.floor(pct * heights.length);

    heights.forEach((h, i) => {
        const bar = document.createElement("div");
        bar.className = "wf-bar" + (i <= activeIdx ? " played" : "") + (i === activeIdx ? " active-bar" : "");
        bar.style.height = `${Math.max(12, Math.min(100, Math.round(h * 100)))}%`;
        bar.dataset.index = i;
        container.appendChild(bar);
    });
}

function setupWaveformControls() {
    const p = $("player");

    const btnPlayPause = $("btnPlayPause");
    if (btnPlayPause) {
        btnPlayPause.onclick = () => {
            if (p.paused) p.play();
            else p.pause();
        };
    }

    p.onplay = () => syncPlayState(true);
    p.onpause = () => syncPlayState(false);

    const btnBack = $("btnSkipBack");
    if (btnBack) btnBack.onclick = () => { p.currentTime = Math.max(0, p.currentTime - 5); };

    const btnFwd = $("btnSkipFwd");
    if (btnFwd) btnFwd.onclick = () => { p.currentTime = Math.min(p.duration || 0, p.currentTime + 5); };

    const volSlider = $("volumeSlider");
    const btnMute = $("btnMute");
    if (volSlider) {
        volSlider.oninput = (e) => {
            p.volume = parseFloat(e.target.value);
            p.muted = p.volume === 0;
            updateVolumeIcon(p.muted || p.volume === 0);
        };
    }
    if (btnMute) {
        btnMute.onclick = () => {
            p.muted = !p.muted;
            if (volSlider) volSlider.value = p.muted ? 0 : p.volume;
            updateVolumeIcon(p.muted);
        };
    }

    const speedPills = document.querySelectorAll(".speed-pill");
    speedPills.forEach((pill) => {
        pill.onclick = () => {
            speedPills.forEach((el) => el.classList.remove("active"));
            pill.classList.add("active");
            p.playbackRate = parseFloat(pill.dataset.speed);
        };
    });
}

function syncPlayState(isPlaying) {
    const iconPlay = $("iconPlay");
    const iconPause = $("iconPause");
    if (iconPlay && iconPause) {
        iconPlay.classList.toggle("hidden", isPlaying);
        iconPause.classList.toggle("hidden", !isPlaying);
    }
}

function updateVolumeIcon(isMuted) {
    const iconHigh = $("iconVolHigh");
    const iconMute = $("iconVolMute");
    if (iconHigh && iconMute) {
        iconHigh.classList.toggle("hidden", isMuted);
        iconMute.classList.toggle("hidden", !isMuted);
    }
}

function setupWaveformInteractions() {
    const container = $("waveformContainer");
    const hoverLine = $("waveformHoverLine");
    const tooltip = $("waveformTooltip");
    const p = $("player");

    if (!container) return;

    let isDragging = false;

    const getTimeFromEvent = (e) => {
        const rect = container.getBoundingClientRect();
        const offsetX = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        const fraction = offsetX / rect.width;
        return { fraction, time: fraction * (p.duration || 0), offsetX, rect };
    };

    container.onmousemove = (e) => {
        const { time, offsetX, rect } = getTimeFromEvent(e);
        if (hoverLine) hoverLine.style.left = `${offsetX}px`;
        if (tooltip) {
            tooltip.textContent = fmtTime(time);
            // Clamp tooltip left offset so text badge never overflows left or right edge
            const halfW = (tooltip.offsetWidth || 34) / 2;
            const clampedX = Math.max(halfW + 4, Math.min(offsetX, rect.width - halfW - 4));
            tooltip.style.left = `${clampedX - offsetX}px`;
        }
        if (isDragging && p.duration) {
            p.currentTime = time;
        }
    };

    container.onmousedown = (e) => {
        isDragging = true;
        const { time } = getTimeFromEvent(e);
        if (p.duration) p.currentTime = time;
    };

    window.addEventListener("mouseup", () => { isDragging = false; });

    window.addEventListener("keydown", (e) => {
        if (e.code === "Space" && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") {
            e.preventDefault();
            if (p.paused) p.play();
            else p.pause();
        }
    });
}

function updateWaveformProgress() {
    const p = $("player");
    const t = p.currentTime;
    const dur = p.duration || 0;

    const curEl = $("wfCurrentTime");
    const durEl = $("wfDuration");
    if (curEl) curEl.textContent = fmtTime(t);
    if (durEl) durEl.textContent = dur ? fmtTime(dur) : "0:00";

    const pct = dur ? t / dur : 0;

    const playhead = $("waveformPlayhead");
    if (playhead) playhead.style.left = `${(pct * 100).toFixed(2)}%`;

    const bars = document.querySelectorAll(".wf-bar");
    if (bars.length) {
        const activeIdx = Math.floor(pct * bars.length);
        bars.forEach((bar, idx) => {
            bar.classList.toggle("played", idx <= activeIdx);
            bar.classList.toggle("active-bar", idx === activeIdx && !p.paused);
        });
    }
}

function setupPlayerSync() {
    const p = $("player");
    p.ontimeupdate = () => {
        const t = p.currentTime;
        
        // Sync Pitch Waveform progress & playhead
        updateWaveformProgress();

        let activeTurn = null;
        for (const turn of document.querySelectorAll(".turn")) {
            const on = t >= +turn.dataset.start && t <= +turn.dataset.end;
            turn.classList.toggle("playing", on);
            if (on) activeTurn = turn;
        }

        // Auto-scroll bottom transcript panel so active turn stays visible on screen!
        if (activeTurn && activeTurn !== lastActiveTurn) {
            lastActiveTurn = activeTurn;
            activeTurn.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
        // A background tab has nothing to show, and over a metered tunnel this
        // loop is the single largest consumer of the request quota.
        if (!currentCall || document.hidden) return;
        const { status, stage, progress } = await api(`/api/calls/${currentCall}/status`);
        $("progressStage").textContent = stage || status;
        $("progressPct").textContent = `${progress}%`;
        $("progressFill").style.width = `${progress}%`;
        if (status === "completed" || status === "failed") {
            clearInterval(pollTimer);
            await loadCall();
        }
        await refreshCalls();
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

loadLimits();
setupUpload();
setupTabs();
setupSearch();
setupPlayerSync();
initSoundbarPlayer();
setupAuth();
refreshHealth();
refreshCalls();
// Idle polling is what burns a tunnel's monthly request quota, so the heartbeat
// is slow and pauses entirely while the tab is in the background.
setInterval(() => { if (!document.hidden) refreshHealth(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshHealth(); });
