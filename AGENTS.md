# Forknook development rules

These constraints are requirements, not optional suggestions.

## Platform-agnostic deployment and updates
- Forknook must run on a standard OCI/Docker-compatible runtime; never require a named hosting panel, orchestration platform, or external updater provider.
- All compatible app/server/UI releases must be installable from **Administration → Updates** within the existing running Forknook container, using its persistent /data volume, integrity-verified release packages, supervised restart, and rollback.
- Do not add a host Docker socket, privileged container, sidecar, companion updater, separately deployed service, or third-party container-management integration. Do not reintroduce vendor-specific update controls.
- Hold runtime dependencies and launcher interfaces stable across normal releases to permit in-container updates. Use compatibility metadata and refuse an unsafe/incompatible bundle before switching the active version.
- Do not claim an unprivileged container can replace its own underlying OCI image. OS-package changes, container-entrypoint changes or immutable runtime changes that truly require an image replacement must be called out explicitly, not silently routed through an external platform.
- Every server/UI update must have tests for release discovery, checksum validation, safe archive handling, permission checks, restart, rollback, and URL-addressable update views.
- Never discard named volumes or existing recipes, users, media, approvals, meal plans, and shopping lists.

## Development discipline
- Commit each completed, verified pass to GitHub main as directed by the project owner.
- Run the entire backend and client build/test suites before declaring an update ready; a GitHub workflow failure blocks release readiness claims.
- Do not remove requested features or introduce unrelated dependencies.
- Android must progress toward a genuinely native Kotlin client. A WebView wrapper must never be represented as feature-complete native Android.
