# Recipe Bridge

Native Android recipe importer for [KitchenOwl](https://kitchenowl.org/) with support for Android sharing and an optional self-hosted Instagram/video extractor.

## Project layout

- `android/` — native Android app and APK build scripts.
- `server/` — optional Docker-hosted speech/OCR extractor.
- `.github/workflows/build-apk.yml` — Android debug APK build and downloadable GitHub Actions artifact.

The app uses an existing [KO-Pellet](https://github.com/Tiscin/ko-pellet) installation for recipe parsing, KitchenOwl authentication, household selection and saving. All proposed recipes are editable and require explicit confirmation.

Android 8+; Android SDK Platform 35 and JDK 17 for local builds. Docker service is optional. Instagram access and actual recipe extraction depend on source availability; inaccessible private posts cannot be reliably imported.

The source tree and complete setup instructions are included in the [initial project archive](https://github.com/gigabytegrove/recipe-importer) as it is being initialized.
