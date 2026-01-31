#!/usr/bin/env bash
set -euo pipefail

DIR="${1:-.}"
OUT="${2:-dump.txt}"

if [[ ! -d "$DIR" ]]; then
  echo "Erro: diretório não existe: $DIR" >&2
  exit 1
fi

: > "$OUT"

find "$DIR" -type f -print0 \
| while IFS= read -r -d '' file; do
    if [[ "$file" == "$OUT" || "$(realpath -m "$file")" == "$(realpath -m "$OUT")" ]]; then
      continue
    fi

    rel="${file#"$DIR"/}"

    printf -- '- %s\n' "$rel" >> "$OUT"
    printf -- '```\n' >> "$OUT"
    cat -- "$file" >> "$OUT" 2>/dev/null || true
    printf -- '\n```\n\n' >> "$OUT"
  done

echo "OK: gerado em $OUT"
