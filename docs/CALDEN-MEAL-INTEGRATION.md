# Calden meal planning integration — source candidate

This integration is additive to RecipeCove's existing family recipe and meal-planning features. **It is not released or production-accepted.** RecipeCove remains the sole source of meal records; Calden has no duplicate meal store or unattended synchronization service.

## Create a scoped integration key

Sign in as a RecipeCove administrator. Open Developer/API settings, choose **Calden: read recipes and manage meal plans**, create a new named API key and copy it once. RecipeCove stores only the hashed token and logs key use. The new key has scope `meals:sync`, distinct from existing `recipes:read` keys. The key can be revoked immediately.

Configure the Calden deployment with `RECIPECOVE_URL=https://your-recipecove-server.example` and `RECIPECOVE_SYNC_KEY=fnk_...`. Never put the key in a browser, Android app, or Calden frontend assets. Calden uses HTTPS by default; on a trusted private network only, `RECIPECOVE_ALLOW_HTTP=true` permits HTTP, which sends credentials without transport encryption.

## Source contract

All new calls require `X-Forknook-API-Key: fnk_...` with scope `meals:sync`.

- `GET /api/v2/integrations/calden/status` — confirms key is valid.
- `GET /api/v2/integrations/calden/recipes` — recipe IDs and titles only.
- `GET /api/v2/integrations/calden/meal-plan?start=YYYY-MM-DD&end=YYYY-MM-DD` — 93-day maximum calendar window.
- `POST /api/v2/integrations/calden/meal-plan` — add a meal. Requires a unique `request_id` for safe retries.
- `PUT /api/v2/integrations/calden/meal-plan/{id}` — change any planned meal, including those originally added directly in RecipeCove.
- `DELETE /api/v2/integrations/calden/meal-plan/{id}` — remove a scheduled meal.

Write requests are authorized only by the separate `meals:sync` key, not standard `recipes:read` or household viewer credentials. Meal mutations are immediately visible in both applications; edits are not copied to a divergent secondary store. An internal keyed idempotency ledger protects repeated POSTs against duplicate inserts, with an SQLite `BEGIN IMMEDIATE` transaction guarding concurrent writers.

## Release gates

- Execute `pytest -q server/tests/test_calden_meal_sync.py` and the existing RecipeCove suite in a prepared environment.
- Verify setup migrations retain all recipes and previously planned meals on an existing SQLite volume.
- Check key revocation, unintended access by `recipes:read`, duplicate concurrent POSTs, edits and deletes from either app.
- Verify Calden Go and browser integration tests, Android compiled build and owner-led device acceptance.
- No version increase or stable release is implied by committing this candidate.
