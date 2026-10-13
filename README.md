# Forknook · family recipe manager

Forknook is a standalone, self-hosted family recipe manager published by Gigabyte Grove. It uses **one low-resource Docker container** for the server, responsive web application, iPad PWA, and API. There is no KitchenOwl authentication or database dependency. The previously developed recipe importer remains integrated with Forknook.

**Release: 2.2.12 · stable.** GitHub: https://github.com/gigabytegrove/forknook

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

## Native Kotlin Android client (2.2.12 stable)

Forknook's Android client is now a separate native **Kotlin + Jetpack Compose** interface. It uses Forknook's `/api/v2` endpoints directly; the Java/WebView wrapper and the old Java-only classes are removed. It uses the same application ID and higher versionCode for upgrade compatibility, and retains the approved Forknook launcher branding.

Native screens cover sign-in, the recipe/photo library, full-page recipe details, recipe creation/editing, share-sheet link/text/photo/video preview, personal made/want status, shopping list creation from selected recipes, shopping check-off, meal planning, approval decisions, recipe directory search, categories, family settings, and basic account creation. The first screen reuses an existing stored Forknook server URL when possible, but users must sign in again with their Forknook account because a WebView session is not interchangeable with a native API token. The product release is designated **stable 2.2.12**, matching the server and web application. Device validation, accessibility polish and fuller native administration remain outstanding and must not be represented as verified simply because the version is stable.

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

The local debug APK is `android/app/build/outputs/versioned/Forknook-v2.2.12-debug.apk`, with an accompanying SHA-256 checksum. Web/server and Android share the 2.2.12 version designation. Android increments its separate versionCode, and its APK build/signing process remains separate. Locally signed debug APKs only update existing installs signed by the same developer key; Android rejects signature mismatches.

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

## Expanded discovery (2.1.8)

**Discover recipes** defaults to **All recipe sources** and searches multiple public recipe publishers plus TheMealDB and Wikibooks. Publisher searches use documented public WordPress REST search endpoints where offered; individual publisher availability, licensing, and result quality can vary. Search results are previews only, never automatically approved or published. Opening an online publisher result extracts the original site's structured recipe and photograph using the existing importer.

**Optional Food.com community index**: Administration → Imports & feeds accepts a user-supplied `RAW_recipes.csv` or a ZIP containing it from the [Food.com Recipes and User Interactions dataset](https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions). Admin must confirm rights to use the dataset. No recipe dataset is bundled or silently downloaded. A streaming import builds `/data/discovery-foodcom.sqlite3` using SQLite FTS5, separate from family data, and can hold approximately 230,000 records. Expect several hundred MB of disk consumption; the dataset is not intended to load entirely in RAM. Search by recipe name or ingredients; click a result to preview and submit through the existing approval process. Imported community records usually do not include a photo; the normal recipe editor permits adding one.

Catalog providers return partial results and identify unavailable external sites rather than presenting a false zero-result claim. No new container, database server, package dependency, platform integration, or subscription is required.

### Live publisher verification

Combined discovery searches use public WordPress REST search APIs from Spend With Pennies, Skinnytaste, The Chunky Chef, Well Plated, The Recipe Critic, Salt & Lavender and Ambitious Kitchen, plus TheMealDB, Wikibooks and any installed offline Food.com collection. Budget Bytes remains selectable individually but is excluded from automatic aggregation because its API sometimes blocks automated clients. Source search results and API availability are not guaranteed, so providers fail independently. Two exact test queries were confirmed externally: Spend With Pennies returned Sticky Bourbon Chicken and Skinnytaste returned Sesame Chicken. Full recipe details are extracted only when a family member explicitly previews a result.

## Version alignment

Web/PWA and server release **v2.2.12 stable** and Android **v2.2.12 stable** use matching product versions. The Android source versionCode is 11 so installed Android 2.2.12-alpha builds with versionCode 6 can be upgraded where the signing key matches. The Windows build produces a **debug-signed** APK, not a release-signed distributable; stable product versioning does not change that signing distinction. Both platforms continue to have their own automated verification and release artifacts.


### Android branding and safe-area layout (2.2.12)
The native Android sign-in and top bar now display the existing approved Forknook PNG logo from the web application (same exact image, unmodified). The green header is replaced by a white header on a warm cream background with terracotta primary actions. Android 15 edge-to-edge rendering uses dark status/navigation icons and Compose `WindowInsets.safeDrawing` once at the application root. The nested Scaffold and NavigationBar do not consume system insets twice. The header therefore appears beneath the phone's clock and notification icons, and content remains within the usable screen bounds. Validate final appearance on actual phones.


### Android Share-to-Forknook support (2.2.12)
Forknook's native Kotlin Android client registers for Android ACTION_SEND text/HTML, images, and video, plus ACTION_SEND_MULTIPLE media shares and Instagram HTTPS Reel/post ACTION_VIEW links. Android's system Share menu—not a button inside Forknook—shows the Forknook destination where the originating app allows it. On receipt the client retains the shared URL/text/content URI, asks for native Forknook login when necessary, displays an Import shared recipe progress screen, sends the source to Forknook's own parsing API, and opens a review/edit screen. Failed extraction retains the incoming share for retry or dismissal. Restricted Instagram posts or inaccessible video sources still cannot be guaranteed extractable; those limitations are reported instead of silently discarding the source. Added Kotlin unit tests for social caption URL extraction to the APK build pipeline. Family approval behavior remains unchanged.


## Release 2.2.12 — everyday cooking improvements

Recipe discovery now offers a read-only preview before importing on web and Android. Android places **Discover** in primary navigation. **Plan to Make** supports choosing a date and, optionally, a meal type. Shopping lists show ingredient names first and preserve the amount separately (for example, **Hot Honey (3 Tbsp)**). Marking an item **Have It** removes it from the active shopping view, while the optional Have It group retains it for undoing the selection.

KitchenOwl re-imports can repair previously missing locally cached photos and restore sectioned Markdown ingredients and instructions when the source provides those fields. Existing edited family recipes are not bulk-overwritten. Migration remains read-only with respect to KitchenOwl. Image restoration is contingent on the source exposing an accessible image URL or authenticated image response; an image cannot be reconstructed when KitchenOwl no longer supplies it.

The web/server update remains self-contained and platform-independent. Native Android builds are separate and use the committed Gradle wrapper; the APK file generated locally is `android/app/build/outputs/versioned/Forknook-v2.2.12-debug.apk`. CI must complete successfully before the release can be considered published or deployment-ready.


## Release 2.2.12 — web session persistence fix

The web client now retains a validated login across browser refreshes using durable same-origin storage. Previously authenticated sessions are migrated on successful verification. Only an authentication rejection invalidates a stored login; transient connection errors or page rendering failures no longer clear it. Logging out still removes the stored token and revokes the server-side session.


## 2.2.12 production UI review

The web sidebar and dashboard use **Plan to Make** terminology, and recipe cards truncate descriptions at a word boundary with an ellipsis. KitchenOwl migration now accepts authenticated same-origin photo URLs with query parameters and does not fetch or cache photographs during migration previews. Previously missing photographs still require a real re-import with valid KitchenOwl credentials and accessible source media. Existing images cannot be fabricated from absent originals.


## Version 2.2.12 — KitchenOwl export repair

The KitchenOwl JSON importer now uses the same source-aware converter as the live API migration. Instead of throwing away ingredients, instructions, notes, time information, and image metadata, it retains compatible fields. Existing imported records can be repaired from an original KitchenOwl JSON export through **Administration → Backups → Repair imported recipes**. Repairs fill missing structured directions and other empty fields; an unchanged original description can be separated into a summary and cooking steps. The operation does not overwrite edited descriptions or existing populated fields. Source photos identified only by bare filenames remain unresolved until the original KitchenOwl media bytes can be retrieved. Migration never invents media URLs or images. Review preview and back up /data before bulk repair.


## 2.2.12 — Review-first recovery from original recipe pages

Approvers and administrators can open any saved recipe with an HTTP(S) source URL and select **Recover from original source**. Forknook uses the existing structured website importer, prefers a longer ingredient list or directions list from the source, retains the existing recipe ID and filled fields, and opens the edit form for explicit review. Nothing is saved until the user submits the existing editor. This is intended for incomplete KitchenOwl exports where ingredient quantities exist on the original recipe page but not in the JSON export. It cannot recover an unavailable or inaccessible website, and users must verify accuracy before saving.


## 2.2.12 — About page and CSP compliance

Added an About Forknook page in desktop and mobile web navigation, with brand identity, publisher, self-hosting/privacy information and GitHub link. Replaced all three web JavaScript-generated inline `style` attributes with stylesheet classes to comply with `style-src 'self'` without relaxing the content security policy. This removes the browser console errors from the administration and update progress panels.


## 2.2.12 — audit baseline route and CSP regression protection

Adds direct-page support for /app/about and regression tests that ensure unrecognized routes are rejected and the strict style CSP remains in force. A full static source security audit, risk register and remaining priorities are in SECURITY_AUDIT_2026-10-13.md. The audit identifies security work still required; this maintenance release does not claim those risks are solved.
