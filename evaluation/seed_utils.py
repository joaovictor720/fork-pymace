import hashlib
import os
import re
from typing import Any


# Keep generated seeds inside signed 32-bit range because the current C++
# applications store seeds as int before passing them to their PRNGs.
MAX_DETERMINISTIC_SEED = 2_147_000_000


def normalize_seed(seed: Any, *, avoid_zero: bool = False) -> int:
    try:
        value = int(seed)
    except (TypeError, ValueError):
        digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], byteorder="big", signed=False)

    normalized = value % MAX_DETERMINISTIC_SEED
    if avoid_zero and normalized == 0:
        return 1
    return normalized


def central_seed(scenario: dict) -> int:
    if "seed" in scenario:
        return normalize_seed(scenario["seed"])
    return normalize_seed(scenario.get("nodes", {}).get("seed", 0))


def derive_seed(base_seed: Any, *stream_parts: Any, avoid_zero: bool = True) -> int:
    parts = ":".join(str(part) for part in stream_parts)
    payload = f"{normalize_seed(base_seed)}:{parts}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    seed = value % MAX_DETERMINISTIC_SEED
    if avoid_zero and seed == 0:
        return 1
    return seed


def _run_number_from_id(run_id: Any) -> int:
    text = str(run_id or "").strip()
    if not text:
        raise ValueError("empty run id")
    if text.isdigit():
        value = int(text)
    else:
        match = re.fullmatch(r"run_(\d+)", text)
        if not match:
            raise ValueError(f"invalid run id: {text!r}")
        value = int(match.group(1))
    if value <= 0:
        raise ValueError(f"invalid run id: {text!r}")
    return value


def effective_seed(scenario: dict, environ: dict = None) -> int:
    seed = central_seed(scenario)
    policy = str(scenario.get("run_seed_policy", "")).strip().lower()
    if policy in {"", "fixed", "scenario"}:
        return seed
    if policy not in {"per_run", "run_id", "central_seed_plus_run_id"}:
        raise ValueError(f"unknown run_seed_policy: {policy!r}")

    env = os.environ if environ is None else environ
    run_id = env.get("MACE_RUN_ID", "")
    if not run_id:
        return seed
    return derive_seed(seed, "run", _run_number_from_id(run_id))
