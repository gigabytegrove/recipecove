# Recipe Importer 1.2.1

**One Docker container. One native Android app. Direct KitchenOwl integration. No KO-Pellet or bridge.**

This self-hosted recipe importer parses regular recipe websites, pasted text, photographs/screenshots, public Instagram Reels and locally shared video files, then displays an editable preview. Recipes are only written to KitchenOwl after a person explicitly confirms the save. The native Android app registers in the Android Share menu, so links shared from Instagram or a browser open directly in Recipe Importer.

## Features

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

Check health with `curl -f http://127.0.0.1:8999/health`; it returns `status: ok`, `version: 1.2.1`, and `kitchenowl_configured: false` until setup. Importing is blocked until installation is complete.

A successful GitHub Actions run publishes `linux/amd64` images to `ghcr.io/gigabytegrove/recipe-importer:latest` and `:1.2.1`. If your image pull times out, that is a registry/download connectivity issue, not an application startup or LLT error. Try the image pull again or build directly from the repository after network connectivity is restored.

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

### Android APK

Source is in `android/`. Android Studio with Android SDK Platform 35, Android Gradle Plugin 8.9.2 and JDK 17 is required to build locally.

Windows PowerShell:

```powershell
cd android
powershell -ExecutionPolicy Bypass -File .\build-apk.ps1
```

Linux/macOS (with SDK environment configured):

```bash
bash android/build-apk.sh
```

Alternatively, from GitHub **Actions → Build Recipe Importer APK → Run workflow**, download the uploaded `recipe-importer-1.1.0-debug` artifact after it succeeds. The APK is for personal sideloading (debug-signed), not for publishing on Google Play. Installing it requires the Android **Install unknown apps** permission for your selected installer.

On the phone: launch Recipe Importer → Settings → enter the URL of **your new Recipe Importer server** → sign in with the KitchenOwl username/password → pick the household. Then share an Instagram Reel, URL, image or video directly to Recipe Importer. Review and edit the preview, check the duplicate warning, and confirm Add to KitchenOwl.

The APK compilation and live Instagram fetching are not verifiable in this source environment; CI and your actual KitchenOwl host will confirm those. Test a regular recipe website and a short public Reel before treating the new service as a complete replacement for your installed KO-Pellet.

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
                         │  Instagram / video + Whisper / FFmpeg  │
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

Unit and API tests mock KitchenOwl and Instagram to avoid network dependencies. `test_short_video_ocr` additionally performs a real short-video OCR test when the relevant host utilities are installed. There are no hardcoded credentials in source. This is version 1.2.1, intended for testing and verification on your network before migrating all recipe imports.

## Recipe URL import and HTTP 403 responses

The URL importer identifies as a web browser rather than advertising a scraper User-Agent. For Food Network it starts with a Firefox-style profile; when a site rejects the first profile with HTTP 403, Recipe Importer retries exactly once with a different profile. Redirect destinations continue to be checked for private or reserved addresses, and the HTML response size limit remains 3 MB. No additional container, headless browser, proxy, or external scraping service is introduced.

Some sites can still prohibit automated access based on network location or other security controls. If both attempts are rejected, the app explains that access was denied and offers text or image import as alternatives. Support for any particular site's blocking policy cannot be guaranteed.
