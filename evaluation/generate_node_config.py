import json
import math
import sys

from seed_utils import central_seed, derive_seed
from spatial_coverage import GridSpec


SPATIAL_WIRE_OVERHEAD_BYTES = 13
MAX_DATAGRAM_BYTES = 1200


def _finite_json_number(value, label):
    """Return a finite JSON number without accepting booleans or strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} must be a finite JSON number".format(label))
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("{} must be a finite JSON number".format(label))
    if not math.isfinite(result):
        raise ValueError("{} must be a finite JSON number".format(label))
    return result


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} must be a positive integer".format(label))
    try:
        numeric_value = float(value)
        integer_value = int(value)
    except (OverflowError, ValueError):
        raise ValueError("{} must be a positive integer".format(label))
    if (
        not math.isfinite(numeric_value)
        or integer_value != numeric_value
        or integer_value <= 0
    ):
        raise ValueError("{} must be a positive integer".format(label))
    return integer_value


def _parse_grid(label, value):
    if not isinstance(value, dict):
        raise ValueError("{} must be a GridSpec object".format(label))
    required = (
        "origin_x_m",
        "origin_y_m",
        "width_m",
        "height_m",
        "rows",
        "cols",
    )
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(
            "{} is missing: {}".format(label, ", ".join(missing))
        )
    return GridSpec(
        origin_x_m=_finite_json_number(
            value["origin_x_m"], label + ".origin_x_m"
        ),
        origin_y_m=_finite_json_number(
            value["origin_y_m"], label + ".origin_y_m"
        ),
        width_m=_finite_json_number(value["width_m"], label + ".width_m"),
        height_m=_finite_json_number(
            value["height_m"], label + ".height_m"
        ),
        rows=value["rows"],
        cols=value["cols"],
    )


def _canonical_grid(scenario, node_config):
    candidates = []
    raw_candidates = [
        ("scenario.grid", scenario.get("grid")),
        ("scenario.grid_spec", scenario.get("grid_spec")),
        ("node_config.grid", node_config.get("grid")),
    ]
    for section_name in ("coverage", "spatial_coverage"):
        section = scenario.get(section_name)
        if section is not None:
            if not isinstance(section, dict):
                raise ValueError("scenario.{} must be an object".format(section_name))
            raw_candidates.append(
                ("scenario.{}.grid".format(section_name), section.get("grid"))
            )
    for label, value in raw_candidates:
        if value is not None:
            candidates.append((label, _parse_grid(label, value)))
    if not candidates:
        raise ValueError(
            "spatial_coverage requires one canonical GridSpec at scenario.grid"
        )
    first_label, grid = candidates[0]
    for label, candidate in candidates[1:]:
        if candidate != grid:
            raise ValueError(
                "conflicting GridSpec definitions: {} and {}".format(
                    first_label, label
                )
            )

    raw_area = scenario["simulation"]["area"]
    if isinstance(raw_area, (list, tuple)):
        if len(raw_area) != 2:
            raise ValueError("simulation.area must contain exactly x and y")
        raw_area_x, raw_area_y = raw_area
    elif isinstance(raw_area, dict):
        if "x" not in raw_area or "y" not in raw_area:
            raise ValueError("simulation.area must contain x and y")
        raw_area_x, raw_area_y = raw_area["x"], raw_area["y"]
    else:
        raise ValueError("simulation.area must be an object or [x, y]")
    area_x = _finite_json_number(raw_area_x, "simulation.area.x")
    area_y = _finite_json_number(raw_area_y, "simulation.area.y")
    if area_x <= 0.0 or area_y <= 0.0:
        raise ValueError("simulation.area dimensions must be positive")
    tolerance = grid.tolerance()
    if not (
        math.isclose(grid.origin_x_m, 0.0, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(grid.origin_y_m, 0.0, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(grid.width_m, area_x, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(grid.height_m, area_y, rel_tol=0.0, abs_tol=tolerance)
    ):
        raise ValueError(
            "GridSpec domain must coincide with simulation.area ([0,x] x [0,y])"
        )
    return {
        "origin_x_m": grid.origin_x_m,
        "origin_y_m": grid.origin_y_m,
        "width_m": grid.width_m,
        "height_m": grid.height_m,
        "rows": grid.rows,
        "cols": grid.cols,
    }, grid

scenario_path = sys.argv[1]
out_path = sys.argv[2]
result_dir = sys.argv[3]

with open(scenario_path, "r", encoding="utf-8") as f:
    sc = json.load(f)

node_cfg = sc["node_config"].copy()
node_count = sc["nodes"]["count"]
seed = central_seed(sc)
application_seed = derive_seed(seed, "application")

udp_port = int(node_cfg.get("udp_port", 5001))
node_cfg["udp_port"] = udp_port

workload = str(
    node_cfg.get("workload", sc.get("workload", "gcounter"))
).strip().lower()
if workload not in ("gcounter", "spatial_coverage"):
    raise ValueError("workload must be gcounter or spatial_coverage")
if "workload" in node_cfg or workload == "spatial_coverage":
    node_cfg["workload"] = workload

if workload == "spatial_coverage":
    # Validate every explicitly supplied interval before applying the legacy
    # diss_per_sec alias, so a malformed shadowed value is not silently ignored.
    for interval_name in ("dissemination_interval", "monitor_interval"):
        if interval_name in node_cfg:
            interval_value = _finite_json_number(
                node_cfg[interval_name], interval_name
            )
            if interval_value <= 0.0:
                raise ValueError("{} must be positive".format(interval_name))
            node_cfg[interval_name] = interval_value

if "diss_per_sec" in node_cfg and node_cfg.get("diss_per_sec") is not None:
    if workload == "spatial_coverage":
        dps = _finite_json_number(node_cfg["diss_per_sec"], "diss_per_sec")
        if dps <= 0.0:
            raise ValueError("diss_per_sec must be positive")
    else:
        # Keep the legacy GCounter coercion/fallback behavior unchanged.
        dps = float(node_cfg["diss_per_sec"])
    if dps > 0.0:
        node_cfg["dissemination_interval"] = 1.0 / dps

if workload == "spatial_coverage":
    grid_dict, grid = _canonical_grid(sc, node_cfg)
    raw_max_datagram_bytes = node_cfg.get(
        "max_datagram_bytes",
        node_cfg.get("max_payload_bytes", MAX_DATAGRAM_BYTES),
    )
    max_datagram_bytes = _positive_integer(
        raw_max_datagram_bytes, "max_datagram_bytes"
    )
    if max_datagram_bytes <= 0 or max_datagram_bytes > MAX_DATAGRAM_BYTES:
        raise ValueError("max_datagram_bytes must be between 1 and 1200")
    worst_case_bytes = 2 * grid.cell_count + SPATIAL_WIRE_OVERHEAD_BYTES
    if worst_case_bytes > max_datagram_bytes:
        raise ValueError(
            "full GSet plus primitive metadata requires {} bytes, budget is {}"
            .format(worst_case_bytes, max_datagram_bytes)
        )

    poll_interval_ms = node_cfg.get(
        "position_poll_interval_ms", node_cfg.get("poll_interval_ms", 100)
    )
    gps_timeout_ms = node_cfg.get("gps_timeout_ms", 50)
    poll_interval_ms = _positive_integer(
        poll_interval_ms, "position_poll_interval_ms"
    )
    gps_timeout_ms = _positive_integer(gps_timeout_ms, "gps_timeout_ms")

    node_cfg["workload"] = "spatial_coverage"
    node_cfg["grid"] = grid_dict
    node_cfg["position_poll_interval_ms"] = poll_interval_ms
    node_cfg["gps_timeout_ms"] = gps_timeout_ms
    gps_socket_template = node_cfg.get(
        "gps_socket_template",
        node_cfg.get("gps_socket_path", "/tmp/node{id}_gps.sock"),
    )
    if not isinstance(gps_socket_template, str) or not gps_socket_template:
        raise ValueError("gps_socket_template must be a non-empty string")
    node_cfg["gps_socket_template"] = gps_socket_template
    node_cfg["max_datagram_bytes"] = max_datagram_bytes

    # These are experimental-control concepts, not application inputs.
    for key in (
        "ops_per_sec",
        "duration",
        "cooldown",
        "post_coverage_window_s",
        "checkpoints",
        "start_delay_s",
        "coverage_start_delay_s",
        "shutdown_grace_s",
        "require_motion_after_cover",
        "t_cover",
        "t_cover_s",
        "experiment_end_trace_s",
        "gps_duration",
        "max_payload_bytes",
        "poll_interval_ms",
        "gps_socket_path",
        "diss_per_sec",
    ):
        node_cfg.pop(key, None)

node_cfg["address"] = {
    str(i): f"10.0.0.{i+1}:{udp_port}"
    for i in range(node_count)
}

node_cfg["central_seed"] = seed
node_cfg["seed"] = application_seed
node_cfg["seed_policy"] = "central_seed_sha256_streams_v1"
node_cfg["seed_streams"] = {
    "node_positions": derive_seed(seed, "node_positions", avoid_zero=False),
    "mobility": derive_seed(seed, "mobility"),
    "application": application_seed,
}
node_cfg["log_dir"] = result_dir

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(node_cfg, f, indent=2)
