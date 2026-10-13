# Forknook source security audit — 2026-10-13

**Baseline:** source at stable v2.2.11 plus audit commits on main.
**Method:** manual source review of server entrypoints, v2 router, importers, local image handling, update verifier, app routes, browser code, Docker, workflows and Android manifest. This is a static audit; no production access, authenticated penetration testing, dependency advisories scan, container image CVE scan, or secrets inspection was performed. Do not interpret this as a clean bill of health.

## Confirmed issue fixed during audit

- **LOW — deep-link 404:** The client advertised /app/about, but server/app.py route allowlist excluded about. Fixed in the audit commit. Regression test added for direct loading and unknown-path rejection.
- **CSP regression protection:** Regression test added to prevent inline style attribute code returning in the client. Existing CSP uses self-only scripts and styles and denies framing.

## Outstanding security findings

1. **HIGH potential — outbound URL fetching can be exposed to DNS rebinding (CWE-918).** server/recipes.py validate_external_url and server/forknook_images.py _public_url each resolve hostnames, verify global IPs, then have httpx perform another DNS lookup before connecting. A malicious public DNS name can change between validation and connection. URL redirect policy, protocols, ports and size caps are good, but do not close this validation/connect gap. **Recommended:** bind the HTTPS/TLS request to the already-validated destination IP using a controlled HTTP transport while retaining SNI and Host semantics; revalidate each redirect; test rebinding against loopback, RFC1918, link-local and metadata targets. Until fixed, restrict which users can submit URL import requests or invoke image capture.
2. **MEDIUM — Android permits cleartext transport globally.** AndroidManifest.xml sets android:usesCleartextTraffic="true". This supports intentional LAN HTTP instances but allows credentials or bearer tokens to traverse unencrypted connections. **Recommended:** prefer HTTPS and an opt-in, prominently warned HTTP/LAN-only compatibility mode; never use HTTP over untrusted networks.
3. **MEDIUM — Android bearer token is stored in ordinary SharedPreferences.** Android ForknookActivity.kt uses getSharedPreferences(\"forknook_native\", MODE_PRIVATE) and saves the session string. Android sandboxing and allowBackup=false reduce exposure, but the token is not explicitly encrypted at rest. **Recommended:** Android Keystore-backed encrypted storage and a secure migration from the existing preference. Ensure Android debug builds do not accidentally ship as production-signed artifacts.\n4. **MEDIUM — browser bearer tokens live in localStorage.** server/web/forknook.js retains a 30-day credential in localStorage. It is available to JavaScript executing on the application origin after a future XSS or compromised same-origin script. The self-only CSP reduces risk but is not a substitute for protecting tokens. **Recommended:** consider server-set Secure, HttpOnly, SameSite cookie with CSRF protection for state-changing requests, migration path for existing clients, and careful Android API compatibility.
5. **MEDIUM — release supply chain uses mutable action tags and elevated publish permissions.** Both .github/workflows workflows reference actions by @vN tags; build-server.yml grants contents:write and packages:write at workflow level. **Recommended:** pin action dependencies by immutable verified SHA or locked workflow dependencies; grant read-only top-level permissions and scope write tokens to publish/release jobs. Enforce branch protection and required checks. See GitHub's Secure use reference.
6. **MEDIUM — dependency and image reproducibility not guaranteed.** server/requirements.txt includes ranged dependencies (yt-dlp, cryptography); Dockerfile uses floating python:3.12-slim, installs apt packages without pinned snapshots. **Recommended:** lock all direct and transitive Python packages for each image and rebuild routinely, record base-image digest/SBOM, scan images/dependency advisories. No vulnerability claims are made for a particular package absent a scan.
7. **LOW–MEDIUM — IP-based login throttling depends on reverse proxy architecture.** server/forknook.py and legacy server/app.py use the direct connected peer address as the rate-limit key. This avoids spoofed forwarded headers but all users behind one reverse proxy can share a bucket. Limits are also process-local. **Recommended:** carefully configure trusted-proxy IP handling and shared rate limiting when multiple workers are used, without trusting arbitrary X-Forwarded-For.
8. **LOW — external media/AI subprocess attack surface.** server/media.py invokes media tools on untrusted uploads and approved external media links; runtime caps and no-shell subprocess execution exist, but vulnerabilities in ffmpeg, yt-dlp, OCR or native libraries require dependency tracking and runtime isolation.

## Positively verified controls

- Session tokens are cryptographically random and hashed for database lookup; session expiry and invalidation paths exist.
- PBKDF2-HMAC SHA-256 password hashing uses salts and 400,000 iterations; setup secrets are stored mode 0600.
- Admin and per-role checks are present on sensitive endpoints; public API keys are stored as hashes, revocable, and read-scoped.
- Image upload enforces supported MIME types, byte limits, Pillow decoding and local generated filenames.
- Standard recipe URL import rejects localhost/private/non-global addresses, userinfo, unusual ports; manually validates redirects and caps HTML downloads. URL image fetch rejects redirects and caps downloads.
- CSP script-src and style-src are self-only; framing disabled; nosniff and no-referrer headers present on app HTML.
- Dockerfile runs as non-root; Compose uses read_only rootfs, no-new-privileges, cap_drop ALL, PID/CPU/memory limits, volumes for persistent data, no docker.sock.
- In-app updater checks SHA-256 checksum; validates tar member paths, types, sizes and duplicates; rejects incompatible runtime/requirements updates and supports rollback.
- Browser recipe fields are rendered via textContent rather than unsafe innerHTML in reviewed code paths.

## Gaps requiring a live production assessment

- Container image CVE scan, effective filesystem rights, published-port reachability, TLS certificate and HTTPS-only policy, proxy headers and auth boundary.
- Admin/regular user privilege escalation test matrix, account reset and password rotation, API key scope and revocation, user enumeration and brute-force behavior behind the actual proxy.
- Adversarial upload fuzzing, SSRF/DNS-rebinding simulation, rate/size DoS, malformed tar corpus, updater restart/rollback in running container.
- Android traffic inspection, token-at-rest storage, network security configuration, exported activity tests and external-intent validation.
- Secret exposure and backup encryption/restore drills, actual audit logging and retention, package SBOM and software composition scanning.

## Priorities

**P0** Close potential DNS rebinding/SSRF gap before trusting URL imports from untrusted contributors. **P1** Lock down Android HTTP behavior and harden GitHub CI permissions/action pinning; evaluate cookie-based browser sessions. **P2** Add continuous dependency scanning, reverse-proxy-aware throttling, security regression suite and production penetration tests.

**Release rule:** source remediation is not production verification. Each fix must pass CI, be packaged as a new stable version, and then be separately installed/tested on production.
