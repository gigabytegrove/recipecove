# Recipe Importer 1.1.0

**One Docker container. One native Android app. Direct KitchenOwl integration. No KO-Pellet or bridge.**

This self-hosted recipe importer parses regular recipe websites, pasted text, photographs/screenshots, public Instagram Reels and locally shared video files, then displays an editable preview. Recipes are only written to KitchenOwl after a person explicitly confirms the save. The native Android app registers in the Android Share menu, so links shared from Instagram or a browser open directly in Recipe Importer.

## Features

- KitchenOwl username/password authentication, per-device sessions, rotating KitchenOwl access tokens, stored server-side in an SQLite volume.
- Household selection, duplicate-name checks, and direct KitchenOwl recipe creation.
- A responsive web app included in **the same Docker container**. No frontend service or separate media-extractor container.
- Ordinary recipe URL parsing from Schema.org JSON-LD `Recipe` and recipe-like page markup; SSRF checks block private or reserved IPs, redirects are individually validated, and response sizes are limited.
- Text and image OCR using a local Tesseract install.
- Instagram public Reel/post extraction using `yt-dlp`, local Whisper transcription, and on-screen OCR sampled by FFmpeg; uploaded videos use the same pipeline. Instagram rate limits, authentication restrictions, and changes to the platform can prevent fetching some posts.
- Optional OpenAI text structuring: set `OPENAI_API_KEY` only if desired. Otherwise the built-in deterministic text parser works for straightforward recipes, but unstructured speech/OCR may need more manual editing. Local video speech/OCR itself requires **no paid API**.
- Native Android APK with share targets for `text/plain`, `image/*`, and `video/*`; source preview and editing prior to save, encrypted session storage on the phone.

## Install the only Docker container

Clone the repository on your Docker host and configure KitchenOwl's address:

```bash
# In the cloned recipe-importer repository
cd server
cp .env.example .env
# Edit .env and set KITCHENOWL_URL to an actual URL reachable from this container.
docker compose up -d --build
curl -f http://127.0.0.1:8999/health
```

The startup health endpoint returns a JSON object with `status: ok` and `version: 1.1.0`. If your selected address is not `127.0.0.1`, change the `curl` address accordingly. Open `http://YOUR-HOST:8999` to access the included browser importer. Sign in with your KitchenOwl credentials. The server does **not** require KO-Pellet and does not connect to it.

An optional GitHub Actions Docker publishing workflow builds an `amd64` image at `ghcr.io/gigabytegrove/recipe-importer:latest` on pushes to `main`. If your GHCR package is accessible to your Docker host, you can switch the compose service from `build: .` to `image: ghcr.io/gigabytegrove/recipe-importer:latest`, then future updates are simply:

```bash
docker compose pull && docker compose up -d
```

You do not need to edit the compose file for each software version. GHCR package accessibility and workflow success must be verified before relying on the published image. The source-build command above always works without GHCR. The container persists its sessions database and Whisper model downloads in named volumes; rebuilds keep those volumes. Back up the `recipe-importer-data` Docker volume, which includes active session tokens.

### Required settings

In `server/.env`: `KITCHENOWL_URL` (required); `PORT`, `BIND_ADDRESS`, and `WHISPER_MODEL` (optional). You may optionally set `OPENAI_API_KEY` and `OPENAI_MODEL` for AI recipe structuring. Without a configured AI API key, the text parser marks unstructured material low-confidence for user review.

Use HTTPS via your existing reverse proxy for access from outside a trusted LAN. A plain `http://` URL sends login credentials and app session tokens unencrypted. Do not expose an HTTP-only importer to the public Internet. Optional Instagram cookie export mounts are commented out in `server/compose.yaml` and must be used only for content you are permitted to access. No login bypass is provided.

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
                         │  authentication and KitchenOwl API     │
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

Unit and API tests mock KitchenOwl and Instagram to avoid network dependencies. `test_short_video_ocr` additionally performs a real short-video OCR test when the relevant host utilities are installed. There are no hardcoded credentials in source. This is version 1.1.0, intended for testing and verification on your network before migrating all recipe imports.
