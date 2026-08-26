import csv
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from classes.mobility.pymobility.models.mobility import (  # noqa: E402
    gauss_markov,
    heterogeneous_truncated_levy_walk,
    random_direction,
    random_walk,
    random_waypoint,
    reference_point_group,
    truncated_levy_walk,
    tvc,
)


DEFAULT_TRACE_INTERVAL_S = 0.2
TRACE_POLICY = "deterministic_mobility_trace_v1"


def deterministic_trace_enabled(mobility_config: Dict[str, Any]) -> bool:
    return bool(mobility_config.get("deterministic_replay", True))


def trace_interval_s(scenario: Dict[str, Any]) -> float:
    mobility = scenario.get("mobility", {})
    node_cfg = scenario.get("node_config", {})
    value = mobility.get(
        "trace_interval",
        node_cfg.get("mobility_trace_interval", DEFAULT_TRACE_INTERVAL_S),
    )
    interval = float(value)
    if interval <= 0:
        raise ValueError("mobility trace interval must be > 0")
    return interval


def trace_duration_s(scenario: Dict[str, Any]) -> float:
    duration = float(scenario.get("simulation", {}).get("duration", 0))
    if duration <= 0:
        raise ValueError("simulation.duration must be > 0 for mobility traces")
    return duration


def _format_float(value: float) -> str:
    return f"{float(value):.9f}"


def _rows_sha256(rows: Iterable[Sequence[str]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update((",".join(row) + "\n").encode("utf-8"))
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_steps(
    model: str,
    dimensions: Tuple[float, float],
    velocity: Tuple[float, float],
    pause: float,
    seed: int,
):
    model_name = model.strip().upper()
    if model_name == "RANDOM_WAYPOINT":
        return random_waypoint(
            1,
            dimensions=dimensions,
            velocity=velocity,
            wt_max=pause,
            seed=seed,
        )
    if model_name == "RANDOM_WALK":
        return random_walk(
            1,
            dimensions=dimensions,
            velocity=velocity[1],
            distance=velocity[1],
            seed=seed,
        )
    if model_name == "TRUNCATED_LEVY":
        return truncated_levy_walk(1, dimensions=dimensions, seed=seed)
    if model_name == "HETEROGENEOUS_TRUNCATED_LEVY":
        return heterogeneous_truncated_levy_walk(
            1,
            dimensions=dimensions,
            seed=seed,
        )
    if model_name == "GAUSS_MARKOV":
        return gauss_markov(1, dimensions=dimensions, seed=seed)
    if model_name == "RANDOM_DIRECTION":
        return random_direction(
            1,
            dimensions=dimensions,
            velocity=velocity,
            wt_max=pause,
            seed=seed,
        )
    if model_name == "REFERENCE_POINT_GROUP":
        return reference_point_group(
            1,
            dimensions=dimensions,
            velocity=velocity,
            seed=seed,
        )
    if model_name == "TVC":
        return tvc(1, dimensions=dimensions, velocity=velocity, seed=seed)
    raise ValueError(
        f"deterministic mobility trace does not support model: {model!r}"
    )


def generate_node_trace(
    out_path: Path,
    *,
    model: str,
    dimensions: Tuple[float, float],
    velocity: Tuple[float, float],
    pause: float,
    seed: int,
    interval_s: float,
    duration_s: float,
) -> Dict[str, Any]:
    steps = _model_steps(model, dimensions, velocity, pause, seed)
    sample_count = int(math.ceil(duration_s / interval_s)) + 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_for_hash: List[Tuple[str, str, str, str]] = []

    with out_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "x_m", "y_m", "z_m"])
        for sample_idx in range(sample_count):
            position = next(steps)
            x = float(position[0][0])
            y = float(position[0][1])
            row = (
                _format_float(sample_idx * interval_s),
                _format_float(x),
                _format_float(y),
                _format_float(0.0),
            )
            rows_for_hash.append(row)
            writer.writerow(row)

    return {
        "file": out_path.name,
        "seed": int(seed),
        "rows": sample_count,
        "sha256": file_sha256(out_path),
        "content_sha256": _rows_sha256(rows_for_hash),
    }


def generate_traces_for_nodes(
    trace_dir: Path,
    *,
    central_seed: int,
    mobility_config: Dict[str, Any],
    dimensions: Tuple[float, float],
    velocities: Sequence[Tuple[float, float]],
    mobility_seeds: Sequence[int],
    interval_s: float,
    duration_s: float,
) -> Dict[str, Any]:
    if trace_dir.exists():
        shutil.rmtree(trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)

    node_entries = []
    for node_idx, (velocity, mobility_seed) in enumerate(
        zip(velocities, mobility_seeds)
    ):
        entry = generate_node_trace(
            trace_dir / f"node_{node_idx}.csv",
            model=str(mobility_config["model"]),
            dimensions=dimensions,
            velocity=velocity,
            pause=float(mobility_config.get("pause", 0)),
            seed=int(mobility_seed),
            interval_s=interval_s,
            duration_s=duration_s,
        )
        entry["node"] = node_idx
        node_entries.append(entry)

    manifest = {
        "policy": TRACE_POLICY,
        "central_seed": int(central_seed),
        "model": str(mobility_config["model"]),
        "interval_s": interval_s,
        "duration_s": duration_s,
        "nodes": node_entries,
    }
    manifest["combined_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()

    manifest_path = trace_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    manifest["manifest_sha256"] = file_sha256(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest
