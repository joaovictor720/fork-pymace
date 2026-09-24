#!/usr/bin/env bash
# Source from runners; keep one explicit interpreter across sudo/SSH/nodes.
MACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MACE_PYTHON="${MACE_PYTHON:-$MACE_ROOT/.venv/bin/python}"
[[ -x "$MACE_PYTHON" ]] || {
  echo "[ERROR] Python not found: $MACE_PYTHON; run setup/setup.sh or set MACE_PYTHON." >&2
  exit 1
}
# Do not resolve the venv symlink: its path selects its site-packages.
MACE_PYTHON="$(cd "$(dirname "$MACE_PYTHON")" && pwd)/$(basename "$MACE_PYTHON")"
export MACE_PYTHON
export PYTHONDONTWRITEBYTECODE=1
export PATH="$(dirname "$MACE_PYTHON"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
# An explicit native setup selects its local build for this kernel. Read a
# literal path, never source generated shell code. Caller overrides win.
if [[ -z "${BATADV_NATIVE_MODULE:-}" && -r "$MACE_ROOT/.build/setup/native-module-$(uname -r)" ]]; then
  IFS= read -r BATADV_NATIVE_MODULE < "$MACE_ROOT/.build/setup/native-module-$(uname -r)"
  export BATADV_NATIVE_MODULE
fi
