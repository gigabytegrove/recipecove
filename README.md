# Recipe Importer 1.4.0

**One Docker container. One native Android app. Direct KitchenOwl integration. No KO-Pellet or bridge.**

This self-hosted recipe importer parses regular recipe websites, pasted text, photographs/screenshots, public Instagram Reels and locally shared video files, then displays an editable preview. Recipes are only written to KitchenOwl after a person explicitly confirms the save. The native Android app registers in the Android Share menu, so links shared from Instagram or a browser open directly in Recipe Importer.

## Features

- **Native in-app server updater** in Settings, with GitHub Release version checks, download/verification progress, one-click restart, health-check rollback, and no Docker socket. Updates modifying Docker dependencies or base image still require Dokploy redeploy.
- **First-run web setup wizard** after Docker deployment: KitchenOwl URL, LLT, administrator password and optional OpenAI key. The container starts before KitchenOwl is configured.
- KitchenOwl username/password authentication for additional users and native Android, plus a dedicated admin sign-in using the configured LLT. Per-device sessions remain in SQLite.
- Household selection, duplicate-name checks, and direct KitchenOwl recipe creation.
- A responsive web app included in **the same Docker container**. No frontend service or separate media-extractor container.
- Ordinary recipe URL parsing from Schema.org JSON-LD `Recipe` and recipe-like page markup; SSRF checks block private or reserved IPs, redirects are individually validated, and response sizes are limited.
- Text and image OCR using a local Tesseract install.
- Instagram public Reel/post extraction using `yt-dlp`, local Whisper transcription, and on-screen OCR sampled by FFmpeg; uploaded videos use the same pipeline. Instagram rate limits, authentication restrictions, and changes to the platform can prevent fetching some posts.
- Optional OpenAI text structuring: enter the API key in the setup wizard only if desired. Otherwise the built-in deterministic text parser works for straightforward recipes, but unstructured speech/OCR may need more manual editing. Local video speech/OCR itself requires **no paid API**.
- Native Android APK with share targets for `text/plain`, `image/*`, and `video/*`; source preview and editing prior to save, encrypted session storage on the phone.

## Deploy the only Docker container

For Dokploy, Portainer, or plain Docker Compose, no KitchenOwl environment variables are needed:

```yaml
services:
  recipe-importer:
    image: ghcr.io/gigabytegrove/recipe-importer:latest
    container_name: recipe-importer
    restart: unless-stopped
    ports:
      - "8999:8999"
    environment:
      WHISPER_MODEL: "base"
      OMP_NUM_THREADS: "1"
      OMP_THREAD_LIMIT: "1"
      OPENBLAS_NUM_THREADS: "1"
      MKL_NUM_THREADS: "1"
    cpus: 1.0
    mem_limit: 1536m
    memswap_limit: 1536m
    volumes:
      - recipe-importer-data:/data
      - whisper-models:/models
      - recipe-importer-temp:/tmp

volumes:
  recipe-importer-data:
  whisper-models:
  recipe-importer-temp:
```

If you build from source instead, `cd server && docker compose up -d --build` uses the matching build configuration without requiring an `.env` file.

### Initial setup (after deployment)

1. Open `http://YOUR-HOST:8999`. A fresh installation shows the **Welcome / first-time setup** wizard.
2. Find the one-time setup code in `docker logs recipe-importer` (look for `Recipe Importer first-run setup code`). The code is regenerated only for a *new* empty data volume and does not disappear across restarts before setup. It is deleted after setup.
3. Enter the **KitchenOwl server URL**, **KitchenOwl long-lived token (LLT)**, and create a **Recipe Importer administrator password** of at least 12 characters. You can optionally enter your OpenAI API key.
4. Click **Test connection**, then **Connect & finish setup**. The server verifies `/api/user` and `/api/household` with the LLT before accepting settings.
5. The administrator is signed in automatically and can begin importing. Additional family members may use their own KitchenOwl usernames/passwords. Android users sign in with KitchenOwl credentials through the existing Android app; they only specify the Recipe Importer server address, never the KitchenOwl address.

To update the KitchenOwl URL or LLT later, sign in as administrator on the web interface and select **Settings**. No container recreation is needed. Configuration, including the LLT and optional OpenAI key, is encrypted in `/data`, with the local encryption key also stored in `/data` (protect backups of this volume and restrict access to Docker host/root). Back up this entire volume; losing its key renders encrypted configuration unreadable. The server never returns stored tokens to browsers in settings/status endpoints. New KitchenOwl settings invalidate previously saved per-user sessions, but the administrator's LLT-based session remains valid.

Check health with `curl -f http://127.0.0.1:8999/health`; it returns `status: ok`, `version: 1.4.0`, and `kitchenowl_configured: false` until setup. Importing is blocked until installation is complete.

A successful GitHub Actions run publishes `linux/amd64` images to `ghcr.io/gigabytegrove/recipe-importer:latest` and `:1.4.0`. If your image pull times out, that is a registry/download connectivity issue, not an application startup or LLT error. Try the image pull again or build directly from the repository after network connectivity is restored.

### Low-memory operation (default)

The default Compose service is designed for a shared 16 GB Docker host, even as more services are consolidated onto it:

- **1 CPU maximum**, **1536 MiB container memory limit** and **no container swap**. These are limits, not idle allocations. No GPU is needed.
- `base` Whisper, INT8 CPU inference with **one inference thread**. It starts in a **temporary subprocess for each video** and fully exits when transcription completes. There is no Whisper model loaded in RAM while the server is idle.
- **One video at a time**; simultaneous requests receive HTTP 429 rather than multiplying memory usage.
- Temporary media/frames/WAVs use the disk-backed `recipe-importer-temp` volume, *not* tmpfs. The volume may contain stale files after a hard crash; it can be cleaned while the container is stopped if needed.
- Transcription may be slow on one CPU. The default speech timeout is 780 seconds; if it exceeds that limit, the importer still tries caption and OCR extraction, and flags missing speech in the editable preview. Very long or difficult clips may exceed this budget.
- The model is downloaded once into the persisted `/models` volume. Persistent storage usage is separate from RAM usage.

The 1536 MiB limit is a **safety ceiling**, not a measured peak or a performance guarantee. For unusually complex videos, the model process can fail due to the limit; the server remains available and warns that the audio could not be transcribed. Start with the defaults and observe `docker stats recipe-importer` during a real Reel import. If inference regularly fails, try `WHISPER_MODEL=tiny` to reduce footprint, or adjust the limit after examining actual usage.

### Configuration and security

No KitchenOwl URL, LLT or OpenAI API key belongs in Compose or `.env`. Those are configured through the web wizard and saved persistently. `WHISPER_MODEL`, threading limits, `PORT`, and `BIND_ADDRESS` remain optional deployment-level performance and networking settings.

A one-time setup code is required to claim a new installation; retrieve it from the container logs. It is a secret and must not be published. Afterwards, an administrator password protects edits to server settings. KitchenOwl logins still retain their own household permissions, and existing Android API routes remain supported. Use HTTPS through a reverse proxy if remotely accessible: login passwords, LLT setup, and device session tokens should never travel over the public Internet using HTTP. An HTTP LAN address is supported for private deployments. Container resources remain capped in Compose.

### Android APK — local build (Calden workflow)

Recipe Importer uses the same **local Git checkout → committed Gradle wrapper → test/build → versioned APK → signature/SHA-256 validation** workflow as Calden. No ZIP-based source bootstrap, GitHub Actions, or desktop copy is needed for local builds.

On Windows, clone `https://github.com/gigabytegrove/recipe-importer.git` once to `$HOME\\recipe-importer`. On later builds, use `git pull --ff-only origin main` in that checkout. From its `android` directory, run `.\\build-apk.ps1` or `.\\gradlew.bat --no-daemon --console=plain clean testDebugUnitTest assembleDebug`.

The Gradle build automatically creates `android/app/build/outputs/versioned/Recipe-Importer-v1.1.0-debug.apk` (version derived from `app/build.gradle`, not hardcoded in the build task). `build-apk.ps1` also checks the APK's package, version, signature, and generates `SHA256SUMS.txt` in the same versioned folder. Requires an existing Android SDK and JDK (the same installation used by Calden). The Windows script installs missing Android SDK Platform 35 via the SDK command-line manager when available, or permits Gradle's own SDK provisioning; you do not need to preinstall Platform 35. The APK is debug-signed for personal sideloading, not a Play Store release.

On the phone: launch Recipe Importer → Settings → enter **your Recipe Importer server URL** → sign in with your KitchenOwl username/password → pick a household. Then share a supported recipe URL, Instagram/Facebook video, screenshot, or local video to Recipe Importer. Review/edit and confirm saving.

## Architecture

```
Android share sheet ──> Recipe Importer Android APK ─┐
                                                    │
Web browser ──> Recipe Importer web UI ──────────────┤
                                                    ▼
                         ┌────────────────────────────────────────┐
                         │    Recipe Importer (ONE container)     │
                         │  first-run setup + KitchenOwl API     │
                         │  HTML/JSON-LD recipe scraper           │
                         │  text + image OCR parser               │
                         │  Facebook + Instagram + uploaded video / Whisper / FFmpeg  │
                         │  optional AI text structuring         │
                         └──────────────────┬─────────────────────┘
                                            │ confirmed saves only
                                            ▼
                                        KitchenOwl
```

## Developer tests

```bash
cd server
python -m pip install -r requirements.txt pytest
python -m pytest -q tests
```

Unit and API tests mock KitchenOwl and Instagram to avoid network dependencies. `test_short_video_ocr` additionally performs a real short-video OCR test when the relevant host utilities are installed. There are no hardcoded credentials in source. This is version 1.4.0, intended for testing and verification on your network before migrating all recipe imports.

## Recipe URL import and HTTP 403 responses

The URL importer identifies as a web browser rather than advertising a scraper User-Agent. For Food Network it starts with a Firefox-style profile; when a site rejects the first profile with HTTP 403, Recipe Importer retries exactly once with a different profile. Redirect destinations continue to be checked for private or reserved addresses, and the HTML response size limit remains 3 MB. No additional container, headless browser, proxy, or external scraping service is introduced.

Some sites can still prohibit automated access based on network location or other security controls. If both attempts are rejected, the app explains that access was denied and offers text or image import as alternatives. Support for any particular site's blocking policy cannot be guaranteed.

## Facebook, Instagram and recipe websites

Pasting a recognized Facebook video, Reel or fb.watch link (including `/page/videos/123` and `/watch/?v=123`) now runs the **video** pipeline rather than extracting a generic page title. Instagram Reel links use the same dispatcher. Ordinary recipe websites continue through structured data and HTML ingredient/instruction extraction. The Android app's existing generic URL endpoint also benefits from this server-side routing.

When a video can be downloaded, Recipe Importer extracts the post caption, audio transcription using the one-shot CPU Whisper subprocess and sampled on-screen text with Tesseract. The preview includes the extracted source evidence for inspection. Without an OpenAI API key, conversational video transcripts may not convert automatically into structured ingredients and steps; in that case the editor clearly reports manual review and displays the captured text rather than falsely claiming that a recipe was imported.

Some Facebook and Instagram links cannot be downloaded, including login-required, region-blocked and platform-restricted posts. Those fail with an explicit message and an alternative to upload a locally available video or paste its caption. Facebook is especially inconsistent and is not guaranteed to work for every public Reel. No browser automation container is required.

### v1.3.1 speech extraction visibility

The recipe preview reports whether video media was downloaded, whether the Whisper worker recognized spoken words, and whether video-frame OCR found readable text. Failed speech extraction is no longer silently reported as a complete video extraction. Empty VAD results are retried once without VAD, with the same single CPU worker and no persistent AI memory. A caption-only Facebook/Instagram fallback explicitly states that video was not downloaded. Use `docker logs --tail 100 recipe-importer` to inspect non-sensitive worker exit codes if speech extraction fails.

## Native application updater — 1.4.0+

Sign in as the Recipe Importer administrator, choose **Settings → Application updates**, and click **Check for updates**. The page shows the current version, newest published GitHub Release, a visible download/verification/restart progress bar, and a rollback button where a previous update exists. **Install update** is enabled only when a newer published version exists. After install, the permanent in-container launcher restarts the web server process and verifies `/health`; if it fails to start, the launcher switches back to the previous version automatically. A copy of each installed release remains in `/data/updates/releases`, and no Docker socket, Docker API access, external auto-updater or other containers are involved. It uses the same lightweight Python process already included in Recipe Importer.

This capability **starts with the v1.4.0 Docker image**: older versions have no launcher/updater and therefore require **one normal pull and redeploy to bootstrap it**. Thereafter compatible Python/UI updates are installed inside the existing container. Every server GitHub Release is built after successful tests and Docker-image publication; its `recipe-importer-server-X.Y.Z.tar.gz` bundle and `.sha256` checksum are downloaded over HTTPS and checked before activation.

**Important Docker limitation:** the updater does not claim to update the immutable Docker image. Releases that alter `requirements.txt`, the Dockerfile, Python/system dependencies, the launcher, or other underlying image requirements are intentionally **blocked by the in-app installer** and must be redeployed using an updated Docker image. The UI reports that incompatibility, rather than pretending a full container image can be replaced from inside an unprivileged container. All KitchenOwl settings, sessions, and downloaded Whisper models remain on persistent volumes across either update path.
