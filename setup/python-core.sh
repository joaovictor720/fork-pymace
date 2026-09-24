#!/usr/bin/env bash
# Repository-local Python and CORE installation. No sudo or network namespaces.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/setup/versions.env"
source "$ROOT_DIR/setup/sources.sh"
[[ $EUID != 0 ]] || { echo "Run local setup as your normal user." >&2; exit 1; }
export PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR="$ROOT_DIR/.build/pip-cache"
PYTHON="${MACE_SETUP_PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "This profile requires Python 3.12 (MACE_SETUP_PYTHON can select it)"'
VENV="$ROOT_DIR/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Existing .venv uses another Python; move it aside before setup"'
"$VENV/bin/python" -m pip install -r "$ROOT_DIR/setup/requirements.txt" -c "$ROOT_DIR/setup/constraints.txt"
mkdir -p "$ROOT_DIR/.build/setup"
CORE_SRC="$ROOT_DIR/.build/sources/core-$CORE_VERSION"
fetch_source "$CORE_SRC" "$CORE_URL" "$CORE_TAG" "$CORE_COMMIT"
fingerprint="$(
  {
    sha256sum "$ROOT_DIR/setup/python-core.sh" "$ROOT_DIR/setup/versions.env" \
      "$ROOT_DIR/setup/requirements.txt" "$ROOT_DIR/setup/constraints.txt"
    printf '%s\n' "$VENV"
    "$VENV/bin/python" -VV
    gcc -dumpmachine
    gcc -dumpfullversion -dumpversion
  } | sha256sum | cut -d' ' -f1
)"
STAMP="$ROOT_DIR/.build/setup/core.sha256"
if [[ "${MACE_SETUP_REBUILD:-0}" != 1 && -f "$STAMP" && "$(cat "$STAMP")" == "$fingerprint" &&
      -x "$VENV/bin/vcmd" && -x "$VENV/bin/vnoded" ]] &&
   "$VENV/bin/python" -c 'from core import constants; from core.emulator.coreemu import CoreEmu; assert constants.COREDPY_VERSION == "9.2.0"' 2>/dev/null; then
  echo "[OK] Reusing CORE $CORE_VERSION installation."
else
  # Build in a disposable clone; cached upstream sources stay pristine.
  build_dir="$(mktemp -d "$ROOT_DIR/.build/core-build.XXXXXX")"
  trap 'rm -rf -- "$build_dir"' EXIT
  git clone --quiet --no-hardlinks "$CORE_SRC" "$build_dir/source"
  ln -s "$VENV" "$build_dir/source/venv"
  (
    cd "$build_dir/source"
    ./bootstrap.sh
    PYTHON="$VENV/bin/python" ./configure --prefix="$VENV" --disable-doc
    make -C netns -j"${BUILD_JOBS:-2}"
    make -C netns install
    make -C daemon/proto
  )
  "$VENV/bin/python" - "$build_dir/source" "$CORE_SRC" <<'PY'
from pathlib import Path
import sys
root, data_root = map(Path, sys.argv[1:])
p = root / "daemon/pyproject.toml"
s = p.read_text().replace('requires = ["poetry>=0.12"]', 'requires = ["poetry-core==2.1.2"]')
s = s.replace('build-backend = "poetry.masonry.api"', 'build-backend = "poetry.core.masonry.api"')
# Generated files are gitignored upstream; explicitly include them in wheels.
s = s.replace('    "core/constants.py",', '    { path = "core/constants.py", format = ["sdist", "wheel"] },')
s = s.replace('    "core/api/grpc/*",', '    { path = "core/api/grpc/*", format = ["sdist", "wheel"] },')
p.write_text(s)
(root / "daemon/core/constants.py").write_text(
    'from pathlib import Path\nCOREDPY_VERSION = "9.2.0"\n'
    f'CORE_CONF_DIR = Path({str(data_root / "package/etc")!r})\n'
    f'CORE_DATA_DIR = Path({str(data_root)!r})\n')
PY
  "$VENV/bin/python" -m pip install --no-build-isolation --no-deps "$build_dir/source/daemon"
  printf '%s\n' "$fingerprint" > "$STAMP"
fi
"$VENV/bin/python" -m pip check
cd "$ROOT_DIR"
"$VENV/bin/python" -c 'import pymace; from core.api.grpc import core_pb2; print("[OK] MACE and CORE import without a display or PAPARAZZI")'
"$VENV/bin/python" -m pip freeze > "$ROOT_DIR/.build/installed-python.txt"
