#!/usr/bin/env bash
set -u
set -o pipefail

TOP="${1:-50}"
TARGET="${2:-/}"
REPORT="${3:-largest_report.txt}"

REPORT_ABS="$(readlink -f "$REPORT" 2>/dev/null || echo "$PWD/$REPORT")"
ERRLOG_ABS="$(readlink -f "${REPORT}.errors.log" 2>/dev/null || echo "$PWD/${REPORT}.errors.log")"

tmp_files="$(mktemp)"
tmp_dirs1="$(mktemp)"
tmp_dirs2="$(mktemp)"
trap 'rm -f "$tmp_files" "$tmp_dirs1" "$tmp_dirs2"' EXIT

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "Gerando relatório... (pode demorar dependendo do disco)"
echo "TOP=$TOP  TARGET=$TARGET"
echo "REPORT=$REPORT_ABS"
echo "ERRLOG=$ERRLOG_ABS"
: > "$ERRLOG_ABS"

# Decide se vai usar sudo
USE_SUDO=1
if command -v sudo >/dev/null 2>&1; then
  if [ -t 0 ]; then
    echo "[1/4] Validando sudo (pode pedir senha)..."
    if ! sudo -v 2>>"$ERRLOG_ABS"; then
      USE_SUDO=0
      echo "Aviso: sudo falhou; vou rodar sem sudo (pode faltar coisas por permissão)."
    fi
  fi
else
  USE_SUDO=0
  echo "Aviso: sudo não existe; vou rodar sem sudo."
fi

run() {
  if [ "$USE_SUDO" -eq 1 ]; then
    sudo "$@"
  else
    "$@"
  fi
}

echo "[2/4] Calculando maiores diretórios (1 nível) em $TARGET ..."
# Não deixa o script morrer por exit code != 0
run du -x -B1 --max-depth=1 "$TARGET" 2>>"$ERRLOG_ABS" \
  | sort -nr | head -n "$TOP" > "$tmp_dirs1" || true

echo "[3/4] Calculando maiores diretórios (até 2 níveis) em /home ..."
if [ -d /home ]; then
  run du -x -B1 --max-depth=2 /home 2>>"$ERRLOG_ABS" \
    | sort -nr | head -n "$TOP" > "$tmp_dirs2" || true
else
  : > "$tmp_dirs2"
fi

echo "[4/4] Calculando maiores arquivos em $TARGET (isso costuma ser o mais lento) ..."
run find "$TARGET" -xdev -type f -printf '%s\t%p\n' 2>>"$ERRLOG_ABS" \
  | sort -nr | head -n "$TOP" > "$tmp_files" || true

{
  echo "Relatório de maiores itens"
  echo "Gerado em: $(ts)"
  echo "Target: $TARGET"
  echo "Top: $TOP"
  echo "Rodou com sudo: $([ "$USE_SUDO" -eq 1 ] && echo "sim" || echo "não")"
  echo

  echo "===================="
  echo "1) Maiores DIRETÓRIOS (1 nível abaixo de $TARGET)"
  echo "(GiB | caminho)"
  if [ -s "$tmp_dirs1" ]; then
    awk '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_dirs1"
  else
    echo "(vazio)"
  fi
  echo

  echo "===================="
  echo "2) Maiores DIRETÓRIOS em /home (até 2 níveis)"
  echo "(GiB | caminho)"
  if [ -s "$tmp_dirs2" ]; then
    awk '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_dirs2"
  else
    echo "(vazio)"
  fi
  echo

  echo "===================="
  echo "3) Maiores ARQUIVOS em $TARGET"
  echo "(GiB | caminho)"
  if [ -s "$tmp_files" ]; then
    awk -F'\t' '{ printf "%10.2f GiB | %s\n", $1/1024/1024/1024, $2 }' "$tmp_files"
  else
    echo "(vazio)"
  fi
  echo

  echo "Erros/avisos detalhados em: $ERRLOG_ABS"
} | tee "$REPORT_ABS" >/dev/null

echo "OK: relatório salvo em: $REPORT_ABS"