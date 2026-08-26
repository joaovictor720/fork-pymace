import hashlib
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
