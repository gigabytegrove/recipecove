#!/usr/bin/env bash
# Update an existing Forknook Compose container in place without losing volumes.
# Does not deploy a second stack, modify Compose files, or remove user data.
set -Eeuo pipefail
IMAGE='ghcr.io/gigabytegrove/forknook:latest'
EXPECTED="${FORKNOOK_TARGET_VERSION:-2.2.3}"
[[ "$EXPECTED" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "ERROR: Invalid Forknook version" >&2; exit 1; }
PINNED_IMAGE="ghcr.io/gigabytegrove/forknook:$EXPECTED"

for executable in docker python3; do
  command -v "$executable" >/dev/null || { echo "ERROR: $executable is required" >&2; exit 1; }
done
docker compose version >/dev/null

echo '===== FORKNOOK SAFE MANUAL DOCKER UPDATE ====='
MATCHES=()
while IFS= read -r id; do
  [[ -n "$id" ]] || continue
  image="$(docker inspect -f '{{.Config.Image}}' "$id")"
  if [[ "$image" == ghcr.io/gigabytegrove/forknook:* ]]; then
    MATCHES+=("$id")
  fi
done < <(docker ps -q)
if (( ${#MATCHES[@]} != 1 )); then
  echo 'ERROR: Expected exactly one running Forknook container using ghcr.io/gigabytegrove/forknook.' >&2
  docker ps --format '{{.Names}}  {{.Image}}'
  exit 1
fi
CID="${MATCHES[0]}"
label() { docker inspect -f "{{ index .Config.Labels \"$1\" }}" "$CID"; }
PROJECT="$(label com.docker.compose.project)"
SERVICE="$(label com.docker.compose.service)"
WORKDIR="$(label com.docker.compose.project.working_dir)"
FILES="$(label com.docker.compose.project.config_files)"
for value in "$PROJECT" "$SERVICE" "$WORKDIR" "$FILES"; do
  [[ -n "$value" && "$value" != '<no value>' ]] || { echo 'ERROR: Compose metadata unavailable; update through Dokploy instead.' >&2; exit 1; }
done
[[ -d "$WORKDIR" ]] || { echo "ERROR: Compose working directory not found: $WORKDIR" >&2; exit 1; }
args=(-p "$PROJECT" --project-directory "$WORKDIR")
IFS=',' read -r -a configs <<< "$FILES"
for file in "${configs[@]}"; do
  [[ "$file" = /* ]] || file="$WORKDIR/$file"
  [[ -f "$file" ]] || { echo "ERROR: Compose file is missing: $file" >&2; exit 1; }
  args+=(-f "$file")
done
if [[ -f "$WORKDIR/.env" ]]; then args+=(--env-file "$WORKDIR/.env"); fi

# Before changing any container, confirm that Compose will deploy the new image.
echo 'Validating the running Compose service and its image reference…'
ACTUAL="$(docker compose "${args[@]}" config --format json | python3 -c '
import json,sys
project=json.load(sys.stdin)
print(project["services"][sys.argv[1]].get("image", ""))
' "$SERVICE")"
if [[ "$ACTUAL" != "$IMAGE" ]]; then
  echo "ERROR: Compose service $SERVICE currently specifies: $ACTUAL" >&2
  echo "Set its image to $IMAGE in Dokploy, save, then rerun this script." >&2
  exit 1
fi
OLD_ID="$(docker inspect -f '{{.Image}}' "$CID")"
OLD_NAME="$(docker inspect -f '{{.Name}}' "$CID")"
echo "Service: $PROJECT/$SERVICE ($OLD_NAME)"
echo "Previous image ID: $OLD_ID"

# An old in-app update must not override the newer immutable image at startup.
# Preserve its pointer on disk for rollback before allowing the new launcher to start.
BACKUP="$(docker exec -i "$CID" python - <<'PY'
import os, time, shutil
from pathlib import Path
pointer=Path('/data/updates/active.json')
if pointer.is_file():
    backup=pointer.with_name('active.before-image-update-%d.json' % int(time.time()))
    shutil.copyfile(pointer, backup)
    pointer.unlink()
    print(str(backup))
else:
    print('')
PY
)"
if [[ -n "$BACKUP" ]]; then echo "Preserved in-app updater pointer at $BACKUP"; fi

echo "Pulling pinned Forknook $EXPECTED image…"
if ! docker pull "$PINNED_IMAGE"; then
  echo 'ERROR: Image pull failed. Your running service has not been recreated.' >&2
  if [[ -n "$BACKUP" ]]; then
    docker exec "$CID" python -c 'import shutil,sys;shutil.copyfile(sys.argv[1],"/data/updates/active.json")' "$BACKUP" || true
  fi
  exit 1
fi

ready() {
  local target="$1" expect="$2"
  docker exec "$target" python -c '
import json,sys,urllib.request
with urllib.request.urlopen("http://127.0.0.1:8999/health",timeout=3) as response:
    obj=json.load(response)
assert obj.get("status")=="ok",obj
assert obj.get("version")==sys.argv[1],obj
' "$expect" >/dev/null 2>&1
}

# Compose still declares :latest. Point that local tag to the verified pinned
# image; --pull never avoids jumping to a newer release during an update test.
docker image tag "$PINNED_IMAGE" "$IMAGE"
echo 'Recreating the existing Compose service (named volumes preserved)…'
if docker compose "${args[@]}" up -d --no-deps --no-build --pull never --force-recreate "$SERVICE"; then
  NEW_CID="$(docker compose "${args[@]}" ps -q "$SERVICE")"
  if [[ -n "$NEW_CID" ]]; then
    for attempt in $(seq 1 60); do
      if ready "$NEW_CID" "$EXPECTED"; then
        echo "SUCCESS: Forknook $EXPECTED is running, with the existing volumes and Compose stack."
        docker exec "$NEW_CID" python -c 'import json,urllib.request;print(json.load(urllib.request.urlopen("http://127.0.0.1:8999/health",timeout=3)))'
        exit 0
      fi
      sleep 2
    done
  fi
fi

echo 'ERROR: New image did not become ready. Attempting to restore the previous image…' >&2
docker tag "$OLD_ID" "$IMAGE"
if [[ -n "$BACKUP" ]]; then
  old_running="$(docker compose "${args[@]}" ps -q "$SERVICE" || true)"
  if [[ -n "$old_running" ]]; then
    docker exec "$old_running" python -c 'import shutil,sys;shutil.copyfile(sys.argv[1],"/data/updates/active.json")' "$BACKUP" || true
  fi
fi
docker compose "${args[@]}" up -d --no-deps --no-build --pull never --force-recreate "$SERVICE" || true
echo 'The previous image was reselected locally. Check the container logs and health before proceeding.' >&2
exit 1
