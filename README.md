# Forknook · family recipe manager

Forknook is a standalone, self-hosted family recipe manager published by Gigabyte Grove. It uses **one low-resource Docker container** for the server, responsive web application, iPad PWA, and API. There is no KitchenOwl authentication or database dependency. The previously developed recipe importer remains integrated with Forknook.

**Release: 2.1.6 · test candidate.** GitHub: https://github.com/gigabytegrove/forknook

## Features

- Local username/password accounts, administrator setup code, 30-day sessions and admin account management.
- Four family roles: **administrator** (settings, migration, API keys and account management), **approver** (recipe review, category management and published recipe editing), **contributor** (submit and import recipes), and **viewer** (read-only family collection). Admins can switch on family-wide recipe approval, while individuals can opt to request review of their own submissions. Approvers accept or decline with a reason. Unapproved suggestions are **not** in the main recipe table.
- Recipe categories, including fourteen sensible defaults, full-page recipe views at `/app/recipes/{id}`, photo cards in the library, ingredients, directions, recipe notes, servings, preparation and cooking time, tags, personal "Want to Make" / "Made Before" tracking.
- Recipes may be imported from structured recipe websites, Facebook and Instagram public videos/reels, feed entries, text, screenshots and uploaded video. Where media is available, local FFmpeg/Whisper/Tesseract extracts caption, speech and on-screen text. Some login-restricted social media cannot be downloaded. Without an optional AI parser, conversational transcripts may require editing. Review the preview before saving.
- Native photo upload and **automatic recipe photo capture**. Public image URLs are fetched when a recipe is saved, normalized with Pillow and stored in the persistent `/data/recipe-images` folder. Missing/rejected photos do not prevent saving; users may upload a photo manually. Photos from recipe sites, available social thumbnails and accessible KitchenOwl recipe image URLs are supported. Recipe photos are session-protected.
- Meal planning, recipe-derived shopping lists, check-off and additional manual shopping items, feeds, and an Administration section for migration and app updates.
- An installable PWA for iPad/iOS Safari with touch-friendly screens and directly bookmarkable routes. Android uses a separate native Kotlin/Compose client, built locally in `android/` with the committed Gradle wrapper and `android/build-apk.ps1` as in Calden.
- Built-in platform-agnostic server/PWA updater for checksum-verified compatible GitHub Releases (single container, no Docker socket, deployment integration or additional service).
- Versioned `/api/v2` API with documented OpenAPI contract (`/api/v2/openapi.json`). Administrators can issue revocable, read-only API keys in `/app/admin/api`. API-key holders may read **only** explicitly published recipes and images for future external integrations; family-only recipes, notes, submissions and account data are not exposed.

## Platform-agnostic container deployment

```yaml
services:
  forknook:
    image: ghcr.io/gigabytegrove/forknook:latest
    pull_policy: always
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
      - forknook-tmp:/tmp
volumes:
  forknook-data:
  forknook-models:
  forknook-tmp:
```

Open `http://YOUR-HOST:8999/app/`, retrieve the first-run setup code from `docker logs forknook`, and create the administrator account. For remote access, use HTTPS with a reverse proxy. The `/data` volume contains accounts, sessions, recipes, settings, downloaded images and updater state. **Back up the entire `/data` volume** (with a consistent SQLite backup/snapshot); backing up only `forknook.sqlite3` omits photographs. Never discard named volumes during an update.

After this first image deployment, compatible code/UI updates are available at **Administration → Updates**, `/app/admin/updates`. Versions with new Docker dependencies or launcher requirements must be deployed by pulling an updated image.

### Emergency recovery image update

Run on the Docker host after verifying its Compose service uses `ghcr.io/gigabytegrove/forknook:latest`:

```bash
curl -fL --retry 3 -o /tmp/forknook-update.sh \
  https://raw.githubusercontent.com/gigabytegrove/forknook/main/scripts/update-existing-forknook.sh
bash -n /tmp/forknook-update.sh && sudo bash /tmp/forknook-update.sh
```

The script identifies exactly one running Forknook Compose service, validates the Compose project and image, pulls the image, recreates **the existing service** without deleting volumes, waits for the intended release health version, and attempts to restore the prior image on startup failure. Read the script before running it. The original KitchenOwl container/database is never touched.

## Family workflow

1. Create users under `/app/admin/users` and assign **Approver** to the family cook, **Contributor** to recipe senders and **Viewer** to family members who should only browse. Admin accounts retain full access.
2. Switch on **Require approval for family contributors** under `/app/admin/review`. A user may separately enable **Request approval before my recipes enter the family collection** at `/app/review`.
3. Contributors import recipes at `/app/sources` or create one from the library. They review text, choose a photo/category, and submit. The recipe is kept in a separate submissions table until approved; approvers see requests at `/app/review` and admins at `/app/admin/review`.
4. Once approved, the recipe appears in `/app/recipes`, where the family can view the full recipe page, mark it made or want-to-make, plan a meal and create a shopping list from selected recipe ingredients.

## KitchenOwl migration (copy-only)

Open **Administration → Imports & feeds** (`/app/admin/imports`). Provide the KitchenOwl instance's base URL and its authorized long-lived access token, load its households and select one. First choose **Preview migration**, then review the counts and choose **Copy recipes** if you want to proceed. The migration reads the household's recipe list and individual records, preserves compatible ingredient/step fields, copies accessible recipe photos, and keeps external source IDs to prevent re-importing the same KitchenOwl recipe twice. Forknook does **not** change or delete anything in KitchenOwl, and the token is not permanently stored in Forknook. Some proprietary KitchenOwl metadata or inaccessible image endpoints may require manual review. Repeat the process for each household, then compare the migrated collection against KitchenOwl before decommissioning it. JSON export import remains under **Administration → Backups**.


## Self-contained application updates

Go to **Administration → Updates** (`/app/admin/updates`). Forknook checks published GitHub Releases, downloads a checksum-verified server/UI package into the existing `/data/updates` directory, verifies file paths and runtime compatibility, and asks its built-in launcher to restart the application. An unsuccessful start triggers automatic rollback. The `/data` family database and photos are preserved. **No Docker socket, platform API, additional service or second container is involved.**

Application and PWA code are updateable inside the container as long as they remain compatible with the installed runtime. Changes requiring a different base image, operating-system packages, or launcher cannot be performed by an unprivileged container without intervention from the host runtime. Forknook reports such a release as incompatible rather than claiming the image itself was updated. For ordinary feature releases, keep runtime dependencies stable to preserve full in-app upgradeability.

The obsolete deployment-platform integration, credentials, UI controls and REST endpoints were removed. On startup, Forknook deletes only the obsolete saved integration credential file; accounts, recipes, shopping lists, photos and update history are untouched.

## Developer API / future integrations

Open **Administration → Developer API** to issue or revoke a dedicated `recipes:read` key for a future external integration. An API key is displayed once; keep it on the **integrating server**, never embed it in front-end JavaScript.

```
GET /api/v2/public/recipes?page=1&limit=20&q=soup
GET /api/v2/public/recipes/{recipe_id}
GET /api/v2/public/recipes/{recipe_id}/image
GET /api/v2/openapi.json
X-Forknook-API-Key: fnk_<secret>
```

Only recipes that an approver/admin marks `published: true` are returned. The response omits internal notes, evidence, users, meal/shopping lists and review history. WordPress should proxy recipe photo requests server-side with its API key. Public publication is not automatic when approving for family use; the two actions are distinct.

## Native Kotlin Android client (2.2.0-alpha)

Forknook's Android client is now a separate native **Kotlin + Jetpack Compose** interface. It uses Forknook's `/api/v2` endpoints directly; the Java/WebView wrapper and the old Java-only classes are removed. It uses the same application ID and higher versionCode for upgrade compatibility, and retains the approved Forknook launcher branding.

Implemented alpha screens cover sign-in, the recipe/photo library, full-page recipe details, recipe creation/editing, share-sheet link/text/photo/video preview, personal made/want status, shopping list creation from selected recipes, shopping check-off, meal planning, approval decisions, recipe directory search, categories, family settings, and basic account creation. The first screen reuses an existing stored Forknook server URL when possible, but users must sign in again with their Forknook account because a WebView session is not interchangeable with a native API token. Full native administration, polishing, accessibility testing, background behavior, and real-device testing are still pending, so this build is an **alpha**, not a production-final client.

To build on the same Windows PC as Calden, open PowerShell:

```powershell
$repo = Join-Path $HOME 'forknook'
if (-not (Test-Path "$repo\.git")) { git clone https://github.com/gigabytegrove/forknook.git $repo }
Set-Location $repo
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'Git update failed.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'android\build-apk.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Android APK build failed.' }
```

The local debug APK is `android/app/build/outputs/versioned/Forknook-v2.2.0-alpha-debug.apk`, with an accompanying SHA-256 checksum. Android builds are independent from the server's 2.1.6 version. Locally signed debug APKs only update existing installs signed by the same developer key; Android rejects signature mismatches.

## Tests and limitations

```bash
python -m pip install -r server/requirements.txt pytest
python -m pytest -q server/tests
node --check server/web/forknook.js
```

Integration tests validate roles, permissions, reviews, migration idempotency, image storage/authorization, publishing, default categories, and routing. Browser/device interaction and real-world KitchenOwl/photo/social-media fetching must still be verified against your actual server and family devices before classifying the release as production-ready. When a site refuses photo downloads, Forknook preserves the recipe but may leave its picture as a placeholder.

## Discover recipes / searchable recipe directories

Open **Discover recipes** (`/app/discover`) to search the Wikibooks Cookbook or TheMealDB by dish name or ingredient. Browse photo cards, preview a candidate, edit the draft and save using the same family approval rules as all other recipes. Nothing is saved by searching or previewing. Catalogs are queried on demand with no indexing daemon or new container. 

**TheMealDB** supplies structured ingredients and photos. Its public V1 key `1` is for personal/development/testing use; for publicly distributed products, the catalog requires a supporter key, configurable as `FORKNOOK_MEALDB_KEY`. Wikibooks uses its openly available community recipe search and may need manual cleanup because page formatting varies; retain source attribution. These sources require internet connectivity and are optional. RSS/Atom subscriptions remain under **Administration → Imports & feeds**, not in the main recipe discovery browser.

When troubleshooting the KitchenOwl migration, a 422 from `/api/household` generally means KitchenOwl rejected the long-lived token (JWT validation). Generate the token within the same KitchenOwl instance, paste only the token, and use that instance's API-accessible URL. Forknook does not retain that token.

The **Administration → Updates** screen distinguishes running server code, embedded image version, and persisted updater state. Application updates require a published checksum-verified release; the built-in updater never controls the host container runtime.

## KitchenOwl long-lived token diagnostics

Forknook locally checks JWT formatting and a complete HMAC signature segment before contacting KitchenOwl. This is a formatting check, not verification of authenticity. Known KitchenOwl validation errors, including signature errors and numeric JWT subject compatibility, are shown without echoing the token. Copy the entire LLT from KitchenOwl and revoke any token exposed in chat or logs. Import is read-only and copy-first.
