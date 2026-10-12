# Forknook — family recipe management system

Forknook is a standalone, self-hosted recipe manager published by **Gigabyte Grove**. It is designed for a household to collect and edit recipes, plan meals, keep personal *Want to Make* and *Made Before* statuses, and turn chosen recipes into shopping lists. **A KitchenOwl account is not required.** Its former Recipe Importer engine is still available for importing from recipe websites, Facebook/Instagram videos when accessible, image/video uploads, and pasted text. All imported recipes are previewed and edited before saving.

**Current server build:** 2.0.2. **Android client:** build from `android/` locally on Windows. The code is intended for on-network testing and review before replacing your existing recipe system; end-to-end behavior with arbitrary social-media links remains dependent on the social platforms.

## Single-container deployment

The published image is `ghcr.io/gigabytegrove/forknook:latest`. The image uses port **8999**, 1 CPU and a 1536 MiB memory limit, with `/data` for persistent SQLite/user settings and `/models` for cached Whisper speech models. The Dockerfile and `server/compose.yaml` define the image build/development counterpart. For Dokploy, create one Compose service with that image and persistent volume mounts:

```yaml
services:
  forknook:
    image: ghcr.io/gigabytegrove/forknook:latest
    container_name: forknook
    restart: unless-stopped
    ports:
      - "8999:8999"
    environment:
      WHISPER_MODEL: base
      OMP_NUM_THREADS: "1"
      OMP_THREAD_LIMIT: "1"
      OPENBLAS_NUM_THREADS: "1"
      MKL_NUM_THREADS: "1"
    cpus: 1.0
    mem_limit: 1536m
    memswap_limit: 1536m
    volumes:
      - forknook-data:/data
      - forknook-models:/models
      - forknook-temp:/tmp
volumes:
  forknook-data:
  forknook-models:
  forknook-temp:
```

**Migrating an existing Recipe Importer container:** do not replace or remove your existing `/data` volume without first backing it up. The new Forknook data store uses `/data/forknook.sqlite3` and does not delete KitchenOwl data. Keep the old Recipe Importer container running independently until migration is tested. A fresh Forknook container needs its own persistent volumes unless you explicitly mount an existing data volume.

## First-run setup and sign-in

Open `/app/` or `/`. On a fresh installation, obtain the **Forknook** first-run setup code from `docker logs forknook` (the log may also contain an unrelated legacy Recipe Importer setup code). Create the first Forknook administrator with a display name, username, and password. Other family members get their own native Forknook account credentials; add them from **Administration → Family accounts**. All accounts connect to the Forknook server, not to KitchenOwl. Sessions are scoped to the persistent SQLite database, and administrators can disable members and change credentials.

## Main screens and direct links

The web app supports real browser URLs: `/app/` (dashboard), `/app/recipes` and `/app/recipes/{id}`, `/app/plan`, `/app/shopping` and `/app/shopping/{id}`, `/app/sources` and `/app/sources/{feed_id}`, `/app/categories`, `/app/admin`, `/app/admin/users`, `/app/admin/backups`, `/app/admin/updates`, and `/app/admin/ai`. Refresh and browser navigation preserve the selected page. The Forknook logo returns to `/app/`.

Features include:

- Native recipe and ingredient/instruction storage in SQLite, categories and tags, full create/edit, status tracking (*Made Before*/*Want to Make*), and recipe search.
- A weekly breakfast/lunch/dinner/snack meal planner linked to saved recipes.
- Editable shopping lists, checked-off items, manual items, and shopping lists generated from selected recipes.
- Recipe URL/RSS/Atom feed subscriptions, browsing feed entries, and preview-first importing.
- Multi-user family accounts with admin/member permissions, native login sessions, and administrative data export.
- Copy-only KitchenOwl JSON migration with dry-run preview and duplicate prevention. KitchenOwl is never deleted or modified by this process. Export a backup before migrating.
- An optional OpenAI text parser for difficult spoken recipes; local Tesseract OCR and the disposable Whisper process remain available without a paid API. Video extraction is best-effort, and source descriptions cannot guarantee video speech/OCR was accessible.

## iPad PWA and Android

The touch-friendly PWA is available under `/app/`. On iPad, open it in Safari and choose **Share → Add to Home Screen**. Static assets are versioned, update using a network-first strategy, and the manifest provides home-screen icons. iPad remote access should use HTTPS for proper PWA/service-worker support.

The Android app opens the same Forknook UI from your own server and accepts links/photos/videos shared from other apps. It requires Forknook credentials—not KitchenOwl credentials. Build the APK locally from the repository on Windows with:

```powershell
Set-Location "$HOME\forknook\android"
.\build-apk.ps1
```

The build uses the checked-in Gradle wrapper, your installed Android SDK/JDK, and saves a versioned APK to `android/app/build/outputs/versioned/`. On a first clone, use `git clone https://github.com/gigabytegrove/forknook.git "$HOME\forknook"`; for later builds, use `git pull --ff-only origin main` from that checkout. Android version and server version are independent.

## Administration and native updates

Open **Administration → Application updates** at `/app/admin/updates`. It checks GitHub Releases for compatible server/UI packages, verifies checksums, shows installation progress, and retains previous release metadata for rollback. System-package, dependency, and launcher changes still require a Docker image redeploy. The administration menu also contains family accounts, data export/migration, and optional AI settings.

### Manual Docker image update if the UI updater is missing

Run `sudo bash scripts/update-existing-forknook.sh` **on the Docker host** from a checkout of this repository, or paste the equivalent command block supplied alongside the update. The script detects exactly one running `ghcr.io/gigabytegrove/forknook` Compose container, validates the `:latest` image in Compose, pulls and recreates only that service, preserves volumes and `/data`, and checks `/health` for version 2.0.2. If health fails, it attempts to return to the previously-running image. If Compose metadata is missing or the image reference differs, it refuses rather than changing your deployment unexpectedly. Back up the `/data` volume before upgrading, and **never** run `docker compose down -v` on a live installation.

The existing KitchenOwl-based Recipe Importer screen remains at `/legacy`, and its API routes remain available for compatibility. Forknook's native features use `/api/v2/` and do not contact KitchenOwl.

## Development and checks

```bash
python -m pip install -r server/requirements.txt pytest
python -m pytest -q server/tests
node --check server/web/forknook.js
bash -n scripts/update-existing-forknook.sh
```

This test suite covers backend permissions, persistence, recipe CRUD, meal planning, shopping lists, migrations, and PWA route responses. It does not prove every function on a physical iPad, Android device, or against every recipe feed/social platform. Preserve a backup and test on a non-production environment before replacing KitchenOwl for your family.
