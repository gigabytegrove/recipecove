# RecipeCove Tablet Mode

Tablet Mode is a cooking-focused view for the RecipeCove web/PWA client, separate from the ordinary recipe view. Open **Recipes → View recipe → Start Tablet Mode** or tap **Cook** directly from the recipe list.

## Cooking features
- Large touch targets; check off ingredients and directions separately.
- Show curated, clearly qualified substitution suggestions for common ingredients without sending recipe text to an AI service.
- Add a personal replacement for any ingredient, whether or not suggestions exist.
- Preserve an unfinished session per recipe and signed-in user **on the current browser/device only**. Changes to a recipe's ingredient/direction arrays reset stale progress.
- A dedicated Reset button clears only the cooking session, never the stored recipe.
- Finish cooking marks the recipe **Made before** using the existing authenticated API, clears local progress only after the server accepts the update, then returns to the recipe.
- Portrait layout stacks ingredients and steps. Landscape on sufficiently wide screens displays them side by side.
- Cooking sessions have their own addressable URL: /app/tablet/{recipe_id}.

This feature does **not** change recipes, alter the ingredient library, or sync live progress between devices. It is not an offline recipe database: opening a recipe requires an accessible RecipeCove server.

## Keep the screen awake
Tablet Mode uses the standard Screen Wake Lock API when available. The API works only where permitted by the operating system and browser, and HTTPS is normally required. The lock is released when leaving Tablet Mode and reacquired after returning to the foreground when permitted. The interface shows the actual result rather than claiming a lock always works.

### iPad Air 2 (iPadOS 15)
**iPadOS 15 cannot support the Screen Wake Lock API**, so a Home Screen web app cannot programmatically disable Auto-Lock on this tablet.

Option 1 — change iPad setting while cooking:
1. Open **Settings → Display & Brightness → Auto-Lock**.
2. Choose **Never**, if available.
3. Return to RecipeCove. Restore your usual timeout after cooking.

Option 2 — Guided Access (especially useful on a wall-mounted tablet):
1. Open **Settings → Accessibility → Guided Access** and enable it.
2. Set a Guided Access passcode.
3. Under **Display Auto-Lock**, choose **Never** (where offered).
4. Open RecipeCove and triple-click the Home button to start a Guided Access session.
5. To finish, triple-click Home and enter the passcode or authenticate using the configured method.

Guided Access keeps the device in the RecipeCove web app while cooking, and helps protect against accidental exits. Keep the tablet powered while using an always-on display.

## Install on an iPad Air 2
1. Update to the latest iPadOS 15 security update offered under Settings → General → Software Update.
2. In **Safari** open the HTTPS URL of your own RecipeCove server followed by /app/.
3. Sign in and choose **Share → Add to Home Screen → Add**. Depending on the Safari version, you may not see a separate "Open as Web App" toggle.
4. Tap the RecipeCove Home Screen icon to launch the app.

The native Android APK cannot be installed on an iPad and RecipeCove does not currently publish a signed iOS IPA. A separate native iPadOS app built and signed for iPadOS 15 would be needed for genuine IPA sideloading. The PWA requires neither an App Store listing nor developer signing.

## Substitution guidance
Substitutions are suggestions, not exact replacements in every recipe. Verify quantities, allergies, dietary restrictions, and cooking chemistry. When no suitable general replacement is known, the interface states that explicitly. Personal substitutions are stored only in the current cooking session.

## Deployment
The app shell and assets are served by the existing RecipeCove container. Release these files through the established verified in-container updater. Do not bypass the updater with host file copies or use an external container administrator.
