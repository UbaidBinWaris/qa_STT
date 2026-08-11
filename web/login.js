const form = document.getElementById("loginForm");
const err = document.getElementById("error");
const btn = document.getElementById("submitBtn");

form.onsubmit = async (e) => {
    e.preventDefault();
    err.classList.add("hidden");
    btn.disabled = true;
    btn.textContent = "Checking…";
    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                // Opt out of ngrok's free-tier interstitial, which would otherwise
                // be returned here as HTML instead of the JSON reply.
                "ngrok-skip-browser-warning": "true",
            },
            body: JSON.stringify({ password: document.getElementById("password").value }),
        });
        const data = await res.json().catch(() => null);
        if (res.ok && data && data.authenticated) {
            window.location.href = "/";
            return;
        }
        err.textContent = (data && data.detail) || `Sign in failed (HTTP ${res.status})`;
    } catch {
        err.textContent = "Server unreachable";
    }
    err.classList.remove("hidden");
    btn.disabled = false;
    btn.textContent = "Sign in";
    document.getElementById("password").select();
};
