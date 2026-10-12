# Recipe Bridge 1.0.0

An Android share-target app for your existing KO-Pellet/KitchenOwl installation, with an optional self-hosted Instagram/video speech + OCR extractor.

## What works in this implementation

- Android **Share → Recipe Bridge** for text, web links, images, and video files.
- Automatic extraction when the app is already connected; otherwise keeps the share and prompts for sign-in.
- KO-Pellet native KitchenOwl login, with a web-login option for other cookie-based sign-in configurations.
- Encrypted session cookies and video extractor token stored in Android Keystore-backed preferences.
- Household chooser, duplicate-title checks, editable recipe preview (ingredients, steps, prep/cook, notes, source), and explicit save.
- Existing KO-Pellet handles ordinary recipe URLs, plain text, images, and KitchenOwl saves.
- Optional video extractor handles public Instagram `/reel/`, `/p/`, `/tv/` links and directly shared video files; transcribes audio with local Whisper, samples video frames for local OCR, and sends extracted text to KO-Pellet's existing `/api/parse/text`.
- Video processing on the Docker host, not the wife's Android device.

**Important limitations:** Instagram can block automated video access and private/login-only posts may fail. Instagram sharing sometimes sends only a link; a direct file share can be an alternative. Local speech/OCR can mishear measurements; KO-Pellet's parsing accuracy varies with its AI setup. Nothing automatically creates a recipe without pressing Save. This project does not bypass Instagram authentication or guarantee extraction of private posts.

## Requirements

- Running KO-Pellet from https://github.com/Tiscin/ko-pellet and a reachable KitchenOwl server.
- KO-Pellet's default native KitchenOwl login OR a working KO-Pellet web login.
- Android 8.0 or later.
- Android Studio with Android SDK Platform 35, and JDK 17 for local builds, OR GitHub Actions.
- Optional video support: Docker Compose host with internet access to load Whisper model and public Instagram content. Approx. 3 GB available memory for video processing.

## Build the installable APK

### Windows

1. Install Android Studio, **Android SDK Platform 35**, and JDK 17 (Android Studio includes a compatible runtime).
2. Extract this ZIP. Open PowerShell in the `android` directory.
3. Run `powershell -ExecutionPolicy Bypass -File .\build-apk.ps1`.
4. The finished APK is `android/recipe-bridge-1.0.0-debug.apk`.
5. Copy the APK to the Android phone and install it with *Install unknown apps* allowed for the file manager.

The PowerShell builder downloads the pinned Gradle distribution, verifies its SHA-256 against Gradle's published checksum, generates an official Gradle wrapper, and builds the APK. The first build needs internet access to download Android Gradle Plugin dependencies. If you already use Android Studio, you can also import `android/` as a project and generate the wrapper with a local Gradle installation.

### Linux/macOS

Set `ANDROID_HOME` to the Android SDK location and run `bash android/build-apk.sh`.

### GitHub Actions

The GitHub Actions workflow is located at `.github/workflows/build-apk.yml`. Push the complete project to the repository root and run **Build Recipe Bridge APK** from the Actions tab. The workflow uploads a `recipe-bridge-1.0.0-debug` APK artifact. It can also be triggered manually from the Actions tab. A debug APK is installable for personal sideloading, but **do not treat it as a signed release APK** for store publication; configure release signing for that separately.

## Optional Docker video extractor

1. On your Docker host, copy `server/` into a directory.
2. Run `cd server && cp .env.example .env`.
3. Edit `.env`: set a private 32-byte random token, e.g. generated using `openssl rand -hex 32`.
4. Run `docker compose up -d --build`.
5. Confirm `curl http://YOUR-DOCKER-HOST:8999/health` returns `{ "status": "ok", ... }`.
6. On your wife's phone in Recipe Bridge → Settings, set the video extractor URL (`http://YOUR-DOCKER-HOST:8999`) and its token; save settings.

The first media import downloads the `base` Whisper model to the persistent Docker volume. Media runs on the Docker host using open-source `faster-whisper`, `ffmpeg`, `tesseract` and `yt-dlp`. There are no required paid AI keys for extraction. KO-Pellet may optionally use your existing AI configuration for higher-quality recipe structuring.

Security: do not expose the extractor unprotected to the public Internet. The bearer token is mandatory. Prefer HTTPS through a reverse proxy for any off-LAN use. Docker drops Linux capabilities, runs as a non-root user, mounts the filesystem read-only, and caps uploaded videos at 80 MB and video length at 15 minutes.

Instagram video links may require cookies to fetch some public/account-restricted posts. If authorized to use a cookie export, mount it to `/run/secrets/instagram_cookies.txt` using the commented Compose volume. Keep that file private and never commit it. Platform changes can break download support over time.

## Using the Android app

1. Launch Recipe Bridge once, open Settings, enter the KO-Pellet URL and sign in.
2. Confirm the desired KitchenOwl household loads; select it.
3. In Instagram, a browser, or the Gallery, press **Share → Recipe Bridge**.
4. The app extracts content and displays a draft. Edit any recipe details.
5. Press **Add to KitchenOwl**, then confirm if a matching recipe title already exists.

If a Reel cannot be fetched, use a locally shared video file, screenshot, or copy-paste the creator's recipe caption. If KO-Pellet cannot produce a useful recipe, manually complete the preview fields before saving. Neither the extractor nor the app promises accuracy from inaccessible content.

## Architecture

```
Android ACTION_SEND
  → Recipe Bridge native Android app
      → ordinary URL / text / image → KO-Pellet existing parse APIs
      → Instagram / video → authenticated optional Docker extractor
          → caption + local Whisper audio + frame OCR → KO-Pellet /api/parse/text
      → editable preview (no automatic saves)
      → KO-Pellet duplicate check
      → POST /api/kitchenowl/recipe/{household_id}
          → KitchenOwl
```

## Tests

Backend unit/API tests (on a development machine):

```bash
cd server
python -m pip install fastapi httpx pytest python-multipart
python -m pytest -q tests
```

Android APK compilation requires the Android SDK and Gradle dependencies. Those are not available in the artifact author's execution environment; therefore this release contains **buildable Android source rather than an already-compiled APK**. Server unit/API tests are run during packaging, but real Instagram fetching and end-to-end KitchenOwl integration require your services and credentials.

## Not in version 1.0

- Offline video analysis directly on Android (intentionally runs on Docker host).
- Bypassing Instagram login/robots/private access restrictions.
- Recipe parsing that magically reconstructs missing steps or amounts. The UI is explicitly for verification.
- Merging with or replacing KO-Pellet's server. It stays unchanged.
