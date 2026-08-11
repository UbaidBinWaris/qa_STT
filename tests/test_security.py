"""Security regression suite run against the live server."""
import os
import sys
import requests

BASE = "http://localhost:8000"
PW = os.environ.get("APP_PASSWORD", "")
results = []


def check(ok, name, detail=""):
    results.append((bool(ok), name, str(detail)[:70]))


# ---------- security headers ----------
anon = requests.Session()
r = anon.get(f"{BASE}/login.html")
h = r.headers
check("script-src 'self'" in h.get("Content-Security-Policy", ""),
      "CSP restricts scripts to self", h.get("Content-Security-Policy", "")[:60])
check("frame-ancestors 'none'" in h.get("Content-Security-Policy", ""),
      "CSP blocks framing (clickjacking)")
check(h.get("X-Content-Type-Options") == "nosniff", "X-Content-Type-Options", h.get("X-Content-Type-Options"))
check(h.get("X-Frame-Options") == "DENY", "X-Frame-Options DENY", h.get("X-Frame-Options"))
check(h.get("Referrer-Policy") == "no-referrer", "Referrer-Policy", h.get("Referrer-Policy"))
check("camera=()" in h.get("Permissions-Policy", ""), "Permissions-Policy")
check("Strict-Transport-Security" not in h, "no HSTS on plain HTTP (correct)")

r_api = anon.get(f"{BASE}/api/auth-status")
check(r_api.headers.get("Cache-Control") == "no-store", "API responses are no-store",
      r_api.headers.get("Cache-Control"))

# HSTS must appear when the request looks like it arrived over TLS
r_tls = anon.get(f"{BASE}/api/auth-status", headers={"x-forwarded-proto": "https"})
check("max-age=31536000" in r_tls.headers.get("Strict-Transport-Security", ""),
      "HSTS present behind TLS", r_tls.headers.get("Strict-Transport-Security"))

# ---------- body size limit ----------
r = anon.post(f"{BASE}/api/login", data=b"x" * 200_000,
              headers={"Content-Type": "application/json"})
check(r.status_code == 413, "oversized JSON body rejected", r.status_code)

# ---------- login hardening ----------
r = anon.post(f"{BASE}/api/login", json={"password": "x" * 5000})
check(r.status_code == 400, "absurd password length rejected", r.status_code)

r = anon.post(f"{BASE}/api/login", json={"password": 12345})
check(r.status_code == 400, "non-string password rejected", r.status_code)

# ---------- successful login + cookie flags ----------
s = requests.Session()
r = s.post(f"{BASE}/api/login", json={"password": PW})
check(r.status_code == 200, "valid login succeeds", r.status_code)
raw = r.headers.get("set-cookie", "")
check("HttpOnly" in raw, "cookie HttpOnly", raw[:60])
check("samesite=strict" in raw.lower(), "cookie SameSite=Strict", raw[-40:])
check("secure" not in raw.lower(), "no Secure flag on plain HTTP (would break local use)")

r_sec = requests.Session().post(f"{BASE}/api/login", json={"password": PW},
                                headers={"x-forwarded-proto": "https"})
check("secure" in r_sec.headers.get("set-cookie", "").lower(),
      "Secure flag set when behind TLS", r_sec.headers.get("set-cookie", "")[:70])

# ---------- authorisation ----------
check(anon.get(f"{BASE}/api/calls").status_code == 401, "anon API blocked")
# A browser navigating to a page gets a redirect; a subresource or API client
# gets 401, so the browser never receives HTML where it expects JavaScript.
r_doc = anon.get(f"{BASE}/", allow_redirects=False,
                 headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"})
check(r_doc.status_code == 302 and r_doc.headers.get("location") == "/login.html",
      "anon page load redirects to login", r_doc.status_code)
r_js = anon.get(f"{BASE}/app.js", allow_redirects=False,
                headers={"Sec-Fetch-Dest": "script"})
check(r_js.status_code == 401, "anon script request gets 401, not HTML", r_js.status_code)
check(anon.get(f"{BASE}/login.js").status_code == 200, "login.js is public (needed pre-auth)")
check(s.get(f"{BASE}/api/calls").status_code == 200, "session can read API")

# ---------- error messages must not leak internals ----------
r = s.get(f"{BASE}/api/search", params={"q": '"unclosed'})
body = r.text.lower()
leaks = any(w in body for w in ("sqlite", "traceback", "/data/github", "fts5"))
check(r.status_code == 400 and not leaks, "malformed search leaks nothing", r.text[:60])

r = s.get(f"{BASE}/api/search", params={"q": "x" * 500})
check(r.status_code == 400, "overlong search rejected", r.status_code)

# ---------- rate limiting ----------
rl = requests.Session()
codes = []
for i in range(12):
    codes.append(rl.post(f"{BASE}/api/login", json={"password": f"wrong{i}"}).status_code)
check(429 in codes, f"login lockout triggers (codes: {codes[:10]})")
after = rl.post(f"{BASE}/api/login", json={"password": PW})
check(after.status_code == 429, "correct password still blocked while locked out", after.status_code)
check(after.headers.get("Retry-After", "").isdigit(), "Retry-After header present",
      after.headers.get("Retry-After"))

# an already-authenticated session must be unaffected by another IP's lockout
check(s.get(f"{BASE}/api/calls").status_code == 200, "existing session unaffected by lockout")

print(f"\n{'':3} {'check':46} detail")
print("-" * 92)
fails = 0
for ok, name, detail in results:
    fails += 0 if ok else 1
    print(f"{'ok ' if ok else 'XX ':3} {name:46} {detail}")
print("-" * 92)
print(f"{len(results)-fails}/{len(results)} passed")
sys.exit(1 if fails else 0)
