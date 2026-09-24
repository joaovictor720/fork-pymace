#!/usr/bin/env bash
# Sourced by setup helpers. Cache only pristine, pinned checkouts.
fetch_source() (
  set -euo pipefail
  local destination="$1" url="$2" tag="$3" commit="$4" temporary=""
  trap '[[ -z "$temporary" ]] || rm -rf -- "$temporary"' EXIT
  if [[ ! -e "$destination" ]]; then
    mkdir -p "$(dirname "$destination")"
    temporary="$(mktemp -d "$(dirname "$destination")/.download.XXXXXX")"
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$url" "$temporary/source"
    [[ "$(git -C "$temporary/source" rev-parse HEAD)" == "$commit" ]] || {
      echo "[ERROR] Downloaded source does not match pinned commit: $url" >&2; exit 1;
    }
    mv "$temporary/source" "$destination"
  fi
  [[ -d "$destination/.git" && "$(git -C "$destination" rev-parse HEAD)" == "$commit" ]] || {
    echo "[ERROR] Unexpected source cache: $destination. Move it aside and retry." >&2; exit 1;
  }
  [[ -z "$(git -C "$destination" status --porcelain --untracked-files=all)" ]] || {
    echo "[ERROR] Modified source cache: $destination. Preserve your changes outside the cache before retrying." >&2; exit 1;
  }
)
