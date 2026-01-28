#!/usr/bin/env bash
set -euo pipefail

TOP="${1:-50}"          # quantos itens mostrar
TARGET="${2:-/}"        # onde varrer (default: /)
REPORT="${3:-largest_report.txt}"

tmp_files="$(mktemp)"
tmp_dirs1="$(mktemp)"
tmp_dirs2="$(mktemp)"
trap 'rm -f "$tmp_files" "$tmp_dirs1" "$tmp_dirs2"' EXIT

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "Gerando relatório... (pode demorar dependendo do disco)"
echo "TOP=$TOP  TARGET=$TARGET  REPORT=$REPORT"

# 1) Maiores diretórios (1 nível) em /
sudo du -x -B1 --max-depth=1 "$TARGET" 2>/dev/null \
  | sort -nr \
  | head -n "$TOP" > "$tmp_dirs1"

# 2) Maiores diretórios (2 níveis) em /home (se existir)
if [ -d /home ]; then
  sudo du -x -B1 --max-depth=2 /home 2>/dev/null \
    | sort -nr \
    | head -n "$TOP" > "$tmp_dirs2"
else
  : > "$tmp_dirs2"
fi

# 3) Maiores arquivos (varre tudo em TARGET)
sudo find "$TARGET" -xdev -type f -printf '%s\t%p\n' 2>/dev/null \
  | sort -nr \
  | head -n "$TOP" > "$tmp_files"

{
  echo "Relatório de maiores itens"
  echo "Gerado em: $(ts)"
  echo "Target: $TARGET"
  echo "Top: $TOP"
  echo

  echo "===================="
  echo "1) Maiores DIRETÓRIOS (1 nível abaixo de $TARGET)"
  echo "(tamanho em GiB | caminho)"
  awk '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_dirs1"
  echo

  echo "===================="
  echo "2) Maiores DIRETÓRIOS em /home (até 2 níveis)"
  echo "(tamanho em GiB | caminho)"
  if [ -s "$tmp_dirs2" ]; then
    awk '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_dirs2"
  else
    echo "(sem /home ou sem dados)"
  fi
  echo

  echo "===================="
  echo "3) Maiores ARQUIVOS em $TARGET"
  echo "(tamanho em GiB | caminho)"
  awk -F'\t' '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_files"
  echo

  echo "Observações rápidas:"
  echo "- Eu usei -x / -xdev pra NÃO atravessar outros filesystems (ex: /mnt, /media, snaps montados)."
  echo "- Erros de permissão são ignorados (2>/dev/null)."
  echo "- Se quiser varrer um diretório específico: ./largest.sh 80 /var"
} | tee "$REPORT"

echo
echo "OK: relatório salvo em: $REPORT"
