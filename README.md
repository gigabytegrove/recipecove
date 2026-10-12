# Forknook · family recipe manager

Forknook is a standalone, self-hosted family recipe manager published by Gigabyte Grove. It uses **one low-resource Docker container** for the server, responsive web application, iPad PWA, and API. There is no KitchenOwl authentication or database dependency. The previously developed recipe importer remains integrated with Forknook.

**Release: 2.1.1 · test candidate.** GitHub: https://github.com/gigabytegrove/forknook

## Features

- Local username/password accounts, administrator setup code, 30-day sessions and admin account management.
- Four family roles: **administrator** (settings, migration, API keys and account management), **approver** (recipe review, category management and published recipe editing), **contributor** (submit and import recipes), and **viewer** (read-only family collection). Admins can switch on family-wide recipe approval, while individuals can opt to request review of their own submissions. Approvers accept or decline with a reason. Unapproved suggestions are **not** in the main recipe table.
- Recipe categories, including fourteen sensible defaults, full-page recipe views at `/app/recipes/{id}`, photo cards in the library, ingredients, directions, recipe notes, servings, preparation and cooking time, tags, personal "Want to Make" / "Made Before" tracking.
- Recipes may be imported from structured recipe websites, Facebook and Instagram public videos/reels, feed entries, text, screenshots and uploaded video. Where media is available, local FFmpeg/Whisper/Tesseract extracts caption, speech and on-screen text. Some login-restricted social media cannot be downloaded. Without an optional AI parser, conversational transcripts may require editing. Review the preview before saving.
- Native photo upload and **automatic recipe photo capture**. Public image URLs are fetched when a recipe is saved, normalized with Pillow and stored in the persistent `/data/recipe-images` folder. Missing/rejected photos do not prevent saving; users may upload a photo manually. Photos from recipe sites, available social thumbnails and accessible KitchenOwl recipe image URLs are supported. Recipe photos are session-protected.
- Meal planning, recipe-derived shopping lists, check-off and additional manual shopping items, feeds, and an Administration section for migration and app updates.
- An installable PWA for iPad/iOS Safari with touch-friendly screens and directly bookmarkable routes. Android builds remain local in `android/`, using the committed Gradle wrapper and `android/build-apk.ps1` as in Calden.
- Built-in GitHub Release updater for compatible server/PWA code releases and a separately provided safe manual Docker image update script for image/launcher changes.
- Versioned `/api/v2` API with documented OpenAPI contract (`/api/v2/openapi.json`). Administrators can issue revocable, read-only API keys in `/app/admin/api`. API-key holders may read **only** explicitly published recipes and images for future WordPress/Gigabyte Grove integration; family-only recipes, notes, submissions and account data are not exposed.

## Docker / Dokploy

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
      - forknook-tmp:/tmp
volumes:
  forknook-data:
  forknook-models:
  forknook-tmp:
```

Open `http://YOUR-HOST:8999/app/`, retrieve the first-run setup code from `docker logs forknook`, and create the administrator account. For remote access, use HTTPS with a reverse proxy. The `/data` volume contains accounts, sessions, recipes, settings, downloaded images and updater state. **Back up the entire `/data` volume** (with a consistent SQLite backup/snapshot); backing up only `forknook.sqlite3` omits photographs. Never discard named volumes during an update.

After this first image deployment, compatible code/UI updates are available at **Administration → Updates**, `/app/admin/updates`. Versions with new Docker dependencies or launcher requirements must be deployed by pulling an updated image.

### Safe manual image update

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

## Developer API / future WordPress plugin

Open **Administration → Developer API** to issue or revoke a dedicated `recipes:read` key. An API key is displayed once; keep it on the **WordPress server**, never embed it in front-end JavaScript.

```
GET /api/v2/public/recipes?page=1&limit=20&q=soup
GET /api/v2/public/recipes/{recipe_id}
GET /api/v2/public/recipes/{recipe_id}/image
GET /api/v2/openapi.json
X-Forknook-API-Key: fnk_<secret>
```

Only recipes that an approver/admin marks `published: true` are returned. The response omits internal notes, evidence, users, meal/shopping lists and review history. WordPress should proxy recipe photo requests server-side with its API key. Public publication is not automatic when approving for family use; the two actions are distinct.

## Android local build on Windows

In PowerShell (from the existing checkout):

```powershell
cd "$HOME\forknook\android"
.\build-apk.ps1
```

If the checkout does not exist, clone `https://github.com/gigabytegrove/forknook.git` first. The debug APK is produced in `android/app/build/outputs/versioned/` and is for sideload testing. It points to the Forknook server, never directly to KitchenOwl. Check the native Android package/version and build output separately from the server's version number.

## Tests and limitations

```bash
python -m pip install -r server/requirements.txt pytest
python -m pytest -q server/tests
node --check server/web/forknook.js
```

Integration tests validate roles, permissions, reviews, migration idempotency, image storage/authorization, publishing, default categories, and routing. Browser/device interaction and real-world KitchenOwl/photo/social-media fetching must still be verified against your actual server and family devices before classifying the release as production-ready. When a site refuses photo downloads, Forknook preserves the recipe but may leave its picture as a placeholder.
