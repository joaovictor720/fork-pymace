#!/usr/bin/env python3
"""Spatial-coverage trace checking and CAR analysis.

This module is the Python side of the spatial contract implemented in
``apps/crdt/common/spatial_coverage.hpp``.  In particular, grid boundaries,
floating-point tolerance and exact-corner traversal must remain identical in
both implementations.

Trace time is relative to the replay epoch.  Event logs use an absolute clock
(Unix time in the spatial workload), so callers must supply that replay epoch
when combining the two.  The analyzer deliberately refuses to guess an epoch:
a plausible-looking CAR computed from unrelated clocks is worse than no result.
"""

from __future__ import print_function

import argparse
import bisect
import csv
import json
import math
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)


CELL_ID_CAPACITY = 1 << 16
DEFAULT_POST_COVERAGE_WINDOW_S = 20.0
DEFAULT_CHECKPOINT_OFFSETS_S = (0.0, 5.0, 10.0, 15.0, 20.0)
_DOUBLE_EPSILON = sys.float_info.epsilon


class TraceFormatError(ValueError):
    """A mobility trace or event log cannot be parsed."""


class TraceValidationError(ValueError):
    """A mobility trace violates the spatial-coverage contract."""

    def __init__(self, issues):
        # type: (Sequence[ValidationIssue]) -> None
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


class TimeAlignmentError(ValueError):
    """Trace-relative and application-event clocks cannot be aligned."""


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class Cell:
    row: int
    col: int
    id: int


PositionLike = Union[Position, Sequence[float]]


def _position(value):
    # type: (PositionLike) -> Position
    if isinstance(value, Position):
        return value
    if isinstance(value, (str, bytes)):
        raise TypeError("position must not be text or bytes")
    try:
        size = len(value)
        if size == 2:
            return Position(float(value[0]), float(value[1]), 0.0)
        if size == 3:
            return Position(float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, OverflowError):
        pass
    raise TypeError("position must be Position or a sequence of two/three numbers")


def _nextafter(value, toward):
    # type: (float, float) -> float
    """Python-3.8-compatible IEEE-754 equivalent of ``math.nextafter``."""
    if math.isnan(value) or math.isnan(toward):
        return float("nan")
    if value == toward:
        return toward
    if value == 0.0:
        # Smallest subnormal binary64, with the direction's sign.
        return struct.unpack("!d", struct.pack("!Q", 1))[0] * (
            -1.0 if toward < 0.0 else 1.0
        )

    bits = struct.unpack("!Q", struct.pack("!d", value))[0]
    if (toward > value) == (value > 0.0):
        bits += 1
    else:
        bits -= 1
    return struct.unpack("!d", struct.pack("!Q", bits))[0]


@dataclass(frozen=True)
class GridSpec:
    origin_x_m: float
    origin_y_m: float
    width_m: float
    height_m: float
    rows: int
    cols: int

    def __post_init__(self):
        # Fail at configuration load instead of much later in an experiment.
        self.validate()

    @property
    def cell_count(self):
        # type: () -> int
        return self.rows * self.cols

    @property
    def cell_width(self):
        # type: () -> float
        return self.width_m / float(self.cols)

    @property
    def cell_height(self):
        # type: () -> float
        return self.height_m / float(self.rows)

    @property
    def upper_x_m(self):
        # type: () -> float
        return self.origin_x_m + self.width_m

    @property
    def upper_y_m(self):
        # type: () -> float
        return self.origin_y_m + self.height_m

    def validate(self):
        # type: () -> None
        values = (
            self.origin_x_m,
            self.origin_y_m,
            self.width_m,
            self.height_m,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise ValueError("GridSpec metric values must be numbers, not booleans or strings")
        try:
            finite = all(math.isfinite(value) for value in values)
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise ValueError("GridSpec values must be finite")
        if self.width_m <= 0.0 or self.height_m <= 0.0:
            raise ValueError("GridSpec width_m and height_m must be > 0")
        if (
            not math.isfinite(self.upper_x_m)
            or not math.isfinite(self.upper_y_m)
            or self.upper_x_m <= self.origin_x_m
            or self.upper_y_m <= self.origin_y_m
        ):
            raise ValueError("GridSpec upper bounds must be finite and representable")
        if (
            isinstance(self.rows, bool)
            or isinstance(self.cols, bool)
            or not isinstance(self.rows, int)
            or not isinstance(self.cols, int)
            or self.rows <= 0
            or self.cols <= 0
        ):
            raise ValueError("GridSpec rows and cols must be positive integers")
        # Check the integer product before converting either dimension to
        # binary64. Python integers are unbounded, unlike C++ uint32_t.
        if self.cell_count > CELL_ID_CAPACITY:
            raise ValueError("GridSpec has more cells than CellId can represent")
        if (
            not math.isfinite(self.cell_width)
            or not math.isfinite(self.cell_height)
            or self.cell_width <= 0.0
            or self.cell_height <= 0.0
        ):
            raise ValueError("GridSpec cell dimensions must be finite and representable")

    def tolerance(self):
        # type: () -> float
        return 1e-9 * max(
            1.0,
            abs(self.origin_x_m),
            abs(self.origin_y_m),
            abs(self.upper_x_m),
            abs(self.upper_y_m),
            self.width_m,
            self.height_m,
        )

    def normalize(self, position):
        # type: (PositionLike) -> Optional[Position]
        point = _position(position)
        if not (
            math.isfinite(point.x)
            and math.isfinite(point.y)
            and math.isfinite(point.z)
        ):
            return None

        epsilon = self.tolerance()
        if (
            point.x < self.origin_x_m - epsilon
            or point.x > self.upper_x_m + epsilon
            or point.y < self.origin_y_m - epsilon
            or point.y > self.upper_y_m + epsilon
        ):
            return None

        x = max(self.origin_x_m, min(point.x, self.upper_x_m))
        y = max(self.origin_y_m, min(point.y, self.upper_y_m))
        # The mathematical upper edge is exclusive, but a value on/just over
        if x >= self.upper_x_m:
            x = _nextafter(self.upper_x_m, self.origin_x_m)
        if y >= self.upper_y_m:
            y = _nextafter(self.upper_y_m, self.origin_y_m)
        return Position(x, y, point.z)

    def locate(self, position):
        # type: (PositionLike) -> Optional[Cell]
        point = self.normalize(position)
        if point is None:
            return None
        col = int(math.floor((point.x - self.origin_x_m) / self.cell_width))
        row = int(math.floor((point.y - self.origin_y_m) / self.cell_height))
        # With a negative origin near zero, nextafter(upper, origin) can be
        # swallowed by the subsequent subtraction.  Normalization has already
        # established that the point belongs to the domain, so an index equal
        # to the dimension is the tolerated upper edge, never an outside cell.
        if col == self.cols:
            col = self.cols - 1
        if row == self.rows:
            row = self.rows - 1
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return None
        return Cell(row=row, col=col, id=row * self.cols + col)

    def cell_id(self, position):
        # type: (PositionLike) -> Optional[int]
        cell = self.locate(position)
        return None if cell is None else cell.id

    def from_id(self, cell_id):
        # type: (int) -> Cell
        if (
            isinstance(cell_id, bool)
            or not isinstance(cell_id, int)
            or cell_id < 0
            or cell_id >= self.cell_count
        ):
            raise ValueError("cell id is outside GridSpec")
        return Cell(
            row=cell_id // self.cols,
            col=cell_id % self.cols,
            id=cell_id,
        )

    def row_col(self, cell_id):
        # type: (int) -> Tuple[int, int]
        cell = self.from_id(cell_id)
        return cell.row, cell.col

    def traverse(self, start, end):
        # type: (PositionLike, PositionLike) -> List[int]
        """Return cells crossed by a segment using canonical 2-D DDA.

        An exact corner advances both axes and therefore adds only the cell
        entered diagonally.  A segment on an internal boundary stays on the
        side selected by floor (right/upper).
        """
        normalized_start = self.normalize(start)
        normalized_end = self.normalize(end)
        if normalized_start is None or normalized_end is None:
            return []
        first = self.locate(normalized_start)
        last = self.locate(normalized_end)
        if first is None or last is None:
            return []

        cells = []  # type: List[int]
        seen = set()  # type: Set[int]

        def append(cell_id):
            # type: (int) -> None
            if cell_id not in seen:
                seen.add(cell_id)
                cells.append(cell_id)

        row = first.row
        col = first.col
        target_row = last.row
        target_col = last.col
        append(first.id)
        if row == target_row and col == target_col:
            return cells

        dx = normalized_end.x - normalized_start.x
        dy = normalized_end.y - normalized_start.y
        step_x = (1 if dx > 0.0 else 0) - (1 if dx < 0.0 else 0)
        step_y = (1 if dy > 0.0 else 0) - (1 if dy < 0.0 else 0)
        infinity = float("inf")

        t_delta_x = infinity
        t_delta_y = infinity
        t_max_x = infinity
        t_max_y = infinity

        if step_x != 0:
            t_delta_x = self.cell_width / abs(dx)
            boundary_x = self.origin_x_m + (
                float(col + 1) if step_x > 0 else float(col)
            ) * self.cell_width
            t_max_x = (boundary_x - normalized_start.x) / dx
            if t_max_x < 0.0 and t_max_x > -1e-14:
                t_max_x = 0.0
        if step_y != 0:
            t_delta_y = self.cell_height / abs(dy)
            boundary_y = self.origin_y_m + (
                float(row + 1) if step_y > 0 else float(row)
            ) * self.cell_height
            t_max_y = (boundary_y - normalized_start.y) / dy
            if t_max_y < 0.0 and t_max_y > -1e-14:
                t_max_y = 0.0

        iteration_limit = self.rows + self.cols + 4
        for _ in range(iteration_limit):
            if row == target_row and col == target_col:
                break

            # These first two cases also avoid treating infinity as a finite
            # corner tie after one axis has reached its target.
            if col == target_col:
                row += step_y
                t_max_y += t_delta_y
            elif row == target_row:
                col += step_x
                t_max_x += t_delta_x
            else:
                scale = max(1.0, abs(t_max_x), abs(t_max_y))
                tie_epsilon = 64.0 * _DOUBLE_EPSILON * scale
                if t_max_x + tie_epsilon < t_max_y:
                    col += step_x
                    t_max_x += t_delta_x
                elif t_max_y + tie_epsilon < t_max_x:
                    row += step_y
                    t_max_y += t_delta_y
                else:
                    col += step_x
                    row += step_y
                    t_max_x += t_delta_x
                    t_max_y += t_delta_y

            if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
                raise RuntimeError("grid traversal escaped a validated GridSpec")
            append(row * self.cols + col)

        if row != target_row or col != target_col:
            raise RuntimeError("grid traversal failed to reach its final cell")
        return cells


def position_to_cell_id(grid, x, y, z=0.0):
    # type: (GridSpec, float, float, float) -> Optional[int]
    """Convenience wrapper for consumers that hold separate coordinates."""
    return grid.cell_id(Position(x, y, z))


def cell_id_to_row_col(grid, cell_id):
    # type: (GridSpec, int) -> Tuple[int, int]
    return grid.row_col(cell_id)


def traverse_segment(grid, start, end):
    # type: (GridSpec, PositionLike, PositionLike) -> List[int]
    return grid.traverse(start, end)


@dataclass(frozen=True)
class TraceSample:
    time_s: float
    position: Position
    node: str
    source: Optional[str] = None
    line_number: Optional[int] = None

    @property
    def x_m(self):
        return self.position.x

    @property
    def y_m(self):
        return self.position.y

    @property
    def z_m(self):
        return self.position.z


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node: Optional[str] = None
    time_s: Optional[float] = None
    source: Optional[str] = None

    def to_dict(self):
        # type: () -> Dict[str, Any]
        result = {"code": self.code, "message": self.message}  # type: Dict[str, Any]
        if self.node is not None:
            result["node"] = self.node
        if self.time_s is not None:
            result["time_s"] = self.time_s
        if self.source is not None:
            result["source"] = self.source
        return result


def _infer_node_from_path(path):
    # type: (Union[str, Path]) -> str
    name = Path(path).name
    match = re.search(r"node[_-]?([^./]+)", name, re.IGNORECASE)
    return match.group(1) if match else Path(path).stem


def _finite_float(value, label, path, line_number):
    # type: (Any, str, Path, int) -> float
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TraceFormatError(
            "{}:{}: {} is not a number: {!r}".format(path, line_number, label, value)
        )
    if not math.isfinite(result):
        raise TraceFormatError(
            "{}:{}: {} must be finite".format(path, line_number, label)
        )
    return result


def read_trace(path, node=None):
    # type: (Union[str, Path], Optional[str]) -> List[TraceSample]
    """Read the canonical ``time_s,x_m,y_m,z_m`` mobility CSV format."""
    trace_path = Path(path)
    inferred_node = str(node) if node is not None else _infer_node_from_path(trace_path)
    samples = []  # type: List[TraceSample]
    try:
        stream = trace_path.open("r", newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise TraceFormatError("cannot open trace {}: {}".format(trace_path, exc))

    with stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            raw_fieldnames = reader.fieldnames
        except csv.Error as exc:
            raise TraceFormatError(
                "{}: invalid CSV header: {}".format(trace_path, exc)
            )
        if raw_fieldnames is None:
            raise TraceFormatError("{}: trace is missing a CSV header".format(trace_path))
        if any(name is None or not name.strip() for name in raw_fieldnames):
            raise TraceFormatError(
                "{}: trace CSV columns must have non-empty names".format(trace_path)
            )
        reader.fieldnames = [name.strip() for name in raw_fieldnames]
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise TraceFormatError("{}: trace has duplicate CSV columns".format(trace_path))
        required = {"time_s", "x_m", "y_m"}
        missing = sorted(required.difference(reader.fieldnames))
        if missing:
            raise TraceFormatError(
                "{}: missing required column(s): {}".format(
                    trace_path, ", ".join(missing)
                )
            )

        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                raise TraceFormatError(
                    "{}:{}: invalid CSV row: {}".format(
                        trace_path, reader.line_num, exc
                    )
                )
            line_number = reader.line_num
            if not row or all(value is None or not str(value).strip() for value in row.values()):
                continue
            if row.get(None):
                raise TraceFormatError(
                    "{}:{}: trace row has more values than its header".format(
                        trace_path, line_number
                    )
                )
            clean = {
                str(key).strip(): (None if value is None else str(value).strip())
                for key, value in row.items()
                if key is not None
            }
            sample_node = clean.get("node") or clean.get("node_id") or inferred_node
            samples.append(
                TraceSample(
                    time_s=_finite_float(
                        clean.get("time_s"), "time_s", trace_path, line_number
                    ),
                    position=Position(
                        _finite_float(clean.get("x_m"), "x_m", trace_path, line_number),
                        _finite_float(clean.get("y_m"), "y_m", trace_path, line_number),
                        _finite_float(
                            clean.get("z_m", "0") or "0",
                            "z_m",
                            trace_path,
                            line_number,
                        ),
                    ),
                    node=str(sample_node),
                    source=str(trace_path),
                    line_number=line_number,
                )
            )
    if not samples:
        raise TraceFormatError("{}: trace contains no samples".format(trace_path))
    return samples


def read_traces(paths):
    # type: (Iterable[Union[str, Path]]) -> Dict[str, List[TraceSample]]
    grouped = {}  # type: Dict[str, List[TraceSample]]
    for path in paths:
        for sample in read_trace(path):
            grouped.setdefault(sample.node, []).append(sample)
    return grouped


def _coerce_trace_mapping(traces):
    # type: (Union[Mapping[str, Iterable[TraceSample]], Iterable[TraceSample]]) -> Dict[str, List[TraceSample]]
    grouped = {}  # type: Dict[str, List[TraceSample]]
    if isinstance(traces, Mapping):
        for node, samples in traces.items():
            grouped.setdefault(str(node), [])
            for sample in samples:
                if not isinstance(sample, TraceSample):
                    raise TypeError("trace entries must be TraceSample instances")
                grouped.setdefault(str(node), []).append(sample)
    else:
        for sample in traces:
            if not isinstance(sample, TraceSample):
                raise TypeError("trace entries must be TraceSample instances")
            grouped.setdefault(sample.node, []).append(sample)
    return grouped


def trace_issues(samples, grid, require_start_at_zero=False):
    # type: (Iterable[TraceSample], GridSpec, bool) -> Tuple[ValidationIssue, ...]
    grouped = _coerce_trace_mapping(samples)
    issues = []  # type: List[ValidationIssue]
    if not grouped:
        return (
            ValidationIssue("empty_trace", "trace contains no samples"),
        )

    for node, node_samples in sorted(grouped.items()):
        if not node_samples:
            issues.append(ValidationIssue("empty_trace", "trace contains no samples", node=node))
            continue
        previous_time = None  # type: Optional[float]
        for index, sample in enumerate(node_samples):
            if not math.isfinite(sample.time_s):
                issues.append(
                    ValidationIssue(
                        "non_finite_time",
                        "trace timestamp must be finite",
                        node=node,
                        source=sample.source,
                    )
                )
            elif sample.time_s < 0.0:
                issues.append(
                    ValidationIssue(
                        "negative_time",
                        "trace timestamp must be >= 0",
                        node=node,
                        time_s=sample.time_s,
                        source=sample.source,
                    )
                )
            if previous_time is not None and sample.time_s <= previous_time:
                issues.append(
                    ValidationIssue(
                        "non_monotonic_time",
                        "timestamps for node {} must be strictly increasing".format(node),
                        node=node,
                        time_s=sample.time_s,
                        source=sample.source,
                    )
                )
            if index == 0 and require_start_at_zero and sample.time_s != 0.0:
                issues.append(
                    ValidationIssue(
                        "nonzero_start",
                        "trace for node {} must start at time 0".format(node),
                        node=node,
                        time_s=sample.time_s,
                        source=sample.source,
                    )
                )
            if grid.normalize(sample.position) is None:
                issues.append(
                    ValidationIssue(
                        "position_out_of_bounds",
                        "position ({}, {}, {}) is outside GridSpec".format(
                            sample.position.x, sample.position.y, sample.position.z
                        ),
                        node=node,
                        time_s=sample.time_s,
                        source=sample.source,
                    )
                )
            previous_time = sample.time_s
    return tuple(issues)


def validate_trace(samples, grid, require_start_at_zero=False):
    # type: (Iterable[TraceSample], GridSpec, bool) -> Tuple[TraceSample, ...]
    materialized = tuple(samples)
    issues = trace_issues(materialized, grid, require_start_at_zero)
    if issues:
        raise TraceValidationError(issues)
    return materialized


@dataclass(frozen=True)
class GroundTruthStep:
    time_s: float
    cell_ids_added: Tuple[int, ...]
    covered_count: int


class GroundTruthTimeline:
    """Monotonic physical ground truth :math:`G(t)` derived only from traces."""

    def __init__(self, grid, first_covered_at):
        # type: (GridSpec, Mapping[int, float]) -> None
        self.grid = grid
        self.first_covered_at = dict(first_covered_at)
        additions = {}  # type: Dict[float, List[int]]
        for cell_id, timestamp in self.first_covered_at.items():
            additions.setdefault(timestamp, []).append(cell_id)

        steps = []  # type: List[GroundTruthStep]
        count = 0
        for timestamp in sorted(additions):
            added = tuple(sorted(additions[timestamp]))
            count += len(added)
            steps.append(GroundTruthStep(timestamp, added, count))
        self.steps = tuple(steps)
        self._cover_times = tuple(sorted(self.first_covered_at.values()))

    def cells_at(self, time_s):
        # type: (float) -> FrozenSet[int]
        return frozenset(
            cell_id
            for cell_id, first_time in self.first_covered_at.items()
            if first_time <= time_s
        )

    def size_at(self, time_s):
        # type: (float) -> int
        return bisect.bisect_right(self._cover_times, time_s)

    def coverage_at(self, time_s):
        # type: (float) -> float
        return self.size_at(time_s) / float(self.grid.cell_count)

    @property
    def t_cover(self):
        # type: () -> Optional[float]
        if len(self.first_covered_at) != self.grid.cell_count:
            return None
        return max(self.first_covered_at.values())

    @property
    def covered_cells(self):
        # type: () -> FrozenSet[int]
        return frozenset(self.first_covered_at)


def _trace_samples_at_or_after(node_samples, start_time_s):
    # type: (Sequence[TraceSample], Optional[float]) -> List[TraceSample]
    if start_time_s is None or not node_samples:
        return list(node_samples)
    times = [sample.time_s for sample in node_samples]
    previous_index = bisect.bisect_right(times, start_time_s) - 1
    if previous_index < 0:
        # The node has no known physical position until its first sample.
        return list(node_samples)

    current = node_samples[previous_index]
    if current.time_s == start_time_s:
        first = current
    else:
        # Replay is piecewise constant between updates.  At measurement start,
        # the most recently applied position is the node's initial coverage.
        first = TraceSample(
            time_s=start_time_s,
            position=current.position,
            node=current.node,
            source=current.source,
            line_number=current.line_number,
        )
    return [first] + list(node_samples[previous_index + 1 :])


def build_ground_truth(traces, grid, coverage_start_time_s=None):
    # type: (Union[Mapping[str, Iterable[TraceSample]], Iterable[TraceSample]], GridSpec, Optional[float]) -> GroundTruthTimeline
    """Build :math:`G(t)`; cells crossed in an interval appear at its end time.

    Replay applies each trace sample atomically at its timestamp.  Assigning all
    DDA cells of ``previous -> current`` to ``current.time_s`` matches that
    observable replay contract.  Contributions from all nodes at the same
    timestamp are grouped naturally by the timeline.
    """
    if coverage_start_time_s is not None and (
        not math.isfinite(coverage_start_time_s) or coverage_start_time_s < 0.0
    ):
        raise ValueError("coverage_start_time_s must be finite and >= 0")
    grouped = _coerce_trace_mapping(traces)
    issues = trace_issues(grouped, grid)
    if issues:
        raise TraceValidationError(issues)

    first_covered_at = {}  # type: Dict[int, float]
    for node in sorted(grouped):
        node_samples = _trace_samples_at_or_after(
            grouped[node], coverage_start_time_s
        )
        previous = None  # type: Optional[TraceSample]
        for sample in node_samples:
            if previous is None:
                cell = grid.locate(sample.position)
                # Validation above guarantees a cell.
                crossed = [cell.id] if cell is not None else []
            else:
                crossed = grid.traverse(previous.position, sample.position)
            for cell_id in crossed:
                old_time = first_covered_at.get(cell_id)
                if old_time is None or sample.time_s < old_time:
                    first_covered_at[cell_id] = sample.time_s
            previous = sample
    return GroundTruthTimeline(grid, first_covered_at)


@dataclass(frozen=True)
class TraceValidationReport:
    valid: bool
    node_count: int
    sample_count: int
    covered_cell_count: int
    total_cell_count: int
    t_cover_s: Optional[float]
    coverage_start_time_s: Optional[float]
    common_end_time_s: Optional[float]
    missing_cell_ids: Tuple[int, ...]
    issues: Tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def coverage_ratio(self):
        # type: () -> float
        return self.covered_cell_count / float(self.total_cell_count)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "valid": self.valid,
            "node_count": self.node_count,
            "sample_count": self.sample_count,
            "covered_cell_count": self.covered_cell_count,
            "total_cell_count": self.total_cell_count,
            "coverage_ratio": self.coverage_ratio,
            "t_cover_s": self.t_cover_s,
            "coverage_start_time_s": self.coverage_start_time_s,
            "common_end_time_s": self.common_end_time_s,
            "missing_cell_ids": list(self.missing_cell_ids),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def check_traces(
    traces,
    grid,
    require_full_coverage=True,
    post_coverage_window_s=DEFAULT_POST_COVERAGE_WINDOW_S,
    require_start_at_zero=True,
    require_motion_after_cover=False,
    coverage_start_time_s=None,
):
    # type: (Union[Mapping[str, Iterable[TraceSample]], Iterable[TraceSample]], GridSpec, bool, Optional[float], bool, bool, Optional[float]) -> TraceValidationReport
    """Validate official traces and report coverage/window readiness."""
    if coverage_start_time_s is not None:
        try:
            coverage_start_time_s = float(coverage_start_time_s)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("coverage_start_time_s must be finite and >= 0")
        if not math.isfinite(coverage_start_time_s) or coverage_start_time_s < 0.0:
            raise ValueError("coverage_start_time_s must be finite and >= 0")
    grouped = _coerce_trace_mapping(traces)
    materialized = [sample for values in grouped.values() for sample in values]
    issues = list(trace_issues(grouped, grid, require_start_at_zero))

    if coverage_start_time_s is not None:
        for node, samples in sorted(grouped.items()):
            if samples and coverage_start_time_s > samples[-1].time_s:
                issues.append(
                    ValidationIssue(
                        "coverage_start_after_trace_end",
                        "coverage starts at {}, after trace for node {} ends at {}".format(
                            coverage_start_time_s, node, samples[-1].time_s
                        ),
                        node=node,
                        time_s=coverage_start_time_s,
                        source=samples[-1].source,
                    )
                )

    timeline = None  # type: Optional[GroundTruthTimeline]
    if not issues:
        timeline = build_ground_truth(
            grouped, grid, coverage_start_time_s=coverage_start_time_s
        )

    covered = 0 if timeline is None else len(timeline.covered_cells)
    missing = tuple(sorted(set(range(grid.cell_count)).difference(
        () if timeline is None else timeline.covered_cells
    )))
    t_cover = None if timeline is None else timeline.t_cover
    ends = [samples[-1].time_s for samples in grouped.values() if samples]
    common_end = min(ends) if ends and len(ends) == len(grouped) else None

    if require_full_coverage and timeline is not None and t_cover is None:
        issues.append(
            ValidationIssue(
                "incomplete_coverage",
                "traces cover {}/{} cells".format(covered, grid.cell_count),
            )
        )

    if post_coverage_window_s is not None:
        if not math.isfinite(post_coverage_window_s) or post_coverage_window_s < 0.0:
            raise ValueError("post_coverage_window_s must be finite and >= 0")
        if t_cover is not None:
            required_end = t_cover + post_coverage_window_s
            for node, samples in sorted(grouped.items()):
                if samples and samples[-1].time_s < required_end:
                    issues.append(
                        ValidationIssue(
                            "post_coverage_window_too_short",
                            "trace for node {} ends at {}, before T_cover + window ({})".format(
                                node, samples[-1].time_s, required_end
                            ),
                            node=node,
                            time_s=samples[-1].time_s,
                            source=samples[-1].source,
                        )
                    )
                if require_motion_after_cover and samples:
                    last_motion_time = None  # type: Optional[float]
                    for previous, current in zip(samples, samples[1:]):
                        if (
                            current.time_s > t_cover
                            and current.position != previous.position
                        ):
                            last_motion_time = current.time_s
                    if last_motion_time is None or last_motion_time < required_end:
                        issues.append(
                            ValidationIssue(
                                "no_motion_through_post_coverage_window",
                                "node {} has no position change reaching T_cover + window".format(node),
                                node=node,
                            )
                        )

    return TraceValidationReport(
        valid=not issues,
        node_count=len(grouped),
        sample_count=len(materialized),
        covered_cell_count=covered,
        total_cell_count=grid.cell_count,
        t_cover_s=t_cover,
        coverage_start_time_s=coverage_start_time_s,
        common_end_time_s=common_end,
        missing_cell_ids=missing,
        issues=tuple(issues),
    )


_ELAPSED_TIME_BASES = frozenset(("elapsed", "relative", "trace", "trace_relative"))


@dataclass(frozen=True)
class ExperimentClock:
    """Shared wall-clock anchors written by the experiment harness."""

    trace_start_unix_s: float
    coverage_start_unix_s: Optional[float] = None
    source: Optional[str] = None

    def __post_init__(self):
        if not math.isfinite(self.trace_start_unix_s):
            raise ValueError("trace_start_unix_s must be finite")
        if self.coverage_start_unix_s is not None:
            if not math.isfinite(self.coverage_start_unix_s):
                raise ValueError("coverage_start_unix_s must be finite")
            if self.coverage_start_unix_s < self.trace_start_unix_s:
                raise ValueError(
                    "coverage_start_unix_s must not precede trace_start_unix_s"
                )

    @property
    def coverage_start_trace_s(self):
        # type: () -> float
        if self.coverage_start_unix_s is None:
            return 0.0
        return self.coverage_start_unix_s - self.trace_start_unix_s

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "trace_start_unix_s": self.trace_start_unix_s,
            "coverage_start_unix_s": self.coverage_start_unix_s,
            "coverage_start_trace_s": self.coverage_start_trace_s,
            "source": self.source,
        }


def read_experiment_clock(path):
    # type: (Union[str, Path]) -> ExperimentClock
    clock_path = Path(path)
    try:
        values = json.loads(clock_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TraceFormatError(
            "cannot open experiment clock {}: {}".format(clock_path, exc)
        )
    except ValueError as exc:
        raise TraceFormatError(
            "{}: invalid experiment clock JSON: {}".format(clock_path, exc)
        )
    if not isinstance(values, dict):
        raise TraceFormatError("{}: experiment clock must be an object".format(clock_path))
    if values.get("schema", "mace_experiment_clock_v1") != "mace_experiment_clock_v1":
        raise TraceFormatError(
            "{}: unsupported experiment clock schema {!r}".format(
                clock_path, values.get("schema")
            )
        )
    if "trace_start_unix_s" not in values:
        raise TraceFormatError(
            "{}: trace_start_unix_s is required".format(clock_path)
        )
    if (
        "coverage_start_unix_s" not in values
        or values["coverage_start_unix_s"] is None
    ):
        raise TraceFormatError(
            "{}: coverage_start_unix_s is required".format(clock_path)
        )
    try:
        trace_start = float(values["trace_start_unix_s"])
        coverage_start = float(values["coverage_start_unix_s"])
        return ExperimentClock(
            trace_start_unix_s=trace_start,
            coverage_start_unix_s=coverage_start,
            source=str(clock_path),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise TraceFormatError("{}: invalid experiment clock: {}".format(clock_path, exc))


def _canonical_time_base(value):
    # type: (str) -> str
    normalized = str(value).strip().lower()
    if normalized in _ELAPSED_TIME_BASES:
        return "elapsed"
    if normalized in ("monotonic", "steady", "steady_clock"):
        return "monotonic"
    if normalized in ("unix", "system", "epoch"):
        return "unix"
    raise TraceFormatError("unknown event-log time base: {!r}".format(value))


def _optional_nonnegative_int(value, label, source, line_number):
    # type: (Any, str, Path, int) -> Optional[int]
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TraceFormatError(
            "{}:{}: {} must be a non-negative integer".format(
                source, line_number, label
            )
        )
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        raise TraceFormatError(
            "{}:{}: {} must be a non-negative integer".format(
                source, line_number, label
            )
        )
    # Reject surprising coercions such as "1.5" while accepting JSON numbers.
    if isinstance(value, float) and value != float(result):
        raise TraceFormatError(
            "{}:{}: {} must be an integer".format(source, line_number, label)
        )
    if result < 0:
        raise TraceFormatError(
            "{}:{}: {} must be >= 0".format(source, line_number, label)
        )
    return result


def _parse_cell_ids(value, source, line_number):
    # type: (Any, Path, int) -> Tuple[int, ...]
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        text_value = str(value).strip()
        if not text_value:
            return ()
        # Canonical C++ output uses '|'; semicolon is accepted for tools that
        # reserve pipes in their log transport.
        parts = re.split(r"[|;]", text_value)

    cell_ids = []  # type: List[int]
    for part in parts:
        cell_id = None  # type: Optional[int]
        if isinstance(part, bool):
            pass
        elif isinstance(part, int):
            cell_id = part
        elif isinstance(part, float):
            if math.isfinite(part) and part.is_integer():
                cell_id = int(part)
        elif isinstance(part, str):
            stripped_part = part.strip()
            if re.fullmatch(r"[+-]?\d+", stripped_part):
                try:
                    cell_id = int(stripped_part)
                except (ValueError, OverflowError):
                    pass
        if cell_id is None:
            raise TraceFormatError(
                "{}:{}: invalid cell id {!r}".format(source, line_number, part)
            )
        if cell_id < 0 or cell_id >= CELL_ID_CAPACITY:
            raise TraceFormatError(
                "{}:{}: cell id {} exceeds uint16_t".format(
                    source, line_number, cell_id
                )
            )
        cell_ids.append(cell_id)
    if len(set(cell_ids)) != len(cell_ids):
        raise TraceFormatError(
            "{}:{}: cell_ids_added contains duplicates".format(source, line_number)
        )
    return tuple(sorted(cell_ids))


@dataclass(frozen=True)
class ReplicaEvent:
    time_s: float
    node: str
    event: str
    replica_size: Optional[int] = None
    cell_ids_added: Tuple[int, ...] = field(default_factory=tuple)
    serialized_state_size: Optional[int] = None
    time_base: str = "unix"
    source: Optional[str] = None
    line_number: Optional[int] = None
    fields: Mapping[str, Any] = field(default_factory=dict, compare=False)
    # Appended to preserve the positional shape of the legacy event model.
    mutation_sequence: Optional[int] = None
    # ``replica_version`` is accepted as a wire/API alias while
    # ``mutation_sequence`` remains the canonical analyzer name.
    replica_version: Optional[int] = field(default=None, repr=False)


def _event_from_mapping(
    values,
    source,
    line_number,
    fallback_node,
    default_time_base,
    positional_time=None,
):
    # type: (Mapping[str, Any], Path, int, str, str, Optional[Any]) -> ReplicaEvent
    clean = {str(key).strip(): value for key, value in values.items()}
    if "elapsed_s" in clean:
        timestamp_value = clean["elapsed_s"]
        inferred_base = "elapsed"
    elif "timestamp_unix_s" in clean:
        timestamp_value = clean["timestamp_unix_s"]
        inferred_base = "unix"
    elif "timestamp_s" in clean:
        timestamp_value = clean["timestamp_s"]
        inferred_base = default_time_base
    elif "time_s" in clean:
        timestamp_value = clean["time_s"]
        inferred_base = default_time_base
    elif "timestamp" in clean:
        timestamp_value = clean["timestamp"]
        inferred_base = default_time_base
    elif positional_time is not None:
        timestamp_value = positional_time
        inferred_base = default_time_base
    else:
        raise TraceFormatError(
            "{}:{}: event has no timestamp".format(source, line_number)
        )

    timestamp = _finite_float(timestamp_value, "event timestamp", source, line_number)
    event_name = str(clean.get("event", "")).strip()
    if not event_name:
        raise TraceFormatError(
            "{}:{}: event field is required".format(source, line_number)
        )
    node = str(clean.get("node", clean.get("node_id", fallback_node))).strip()
    if not node:
        raise TraceFormatError(
            "{}:{}: node field is required".format(source, line_number)
        )
    time_base = _canonical_time_base(clean.get("time_base", inferred_base))

    mutation_sequence = _optional_nonnegative_int(
        clean.get("mutation_sequence"),
        "mutation_sequence",
        source,
        line_number,
    )
    replica_version = _optional_nonnegative_int(
        clean.get("replica_version"),
        "replica_version",
        source,
        line_number,
    )
    if (
        mutation_sequence is not None
        and replica_version is not None
        and mutation_sequence != replica_version
    ):
        raise TraceFormatError(
            "{}:{}: mutation_sequence and replica_version disagree".format(
                source, line_number
            )
        )

    return ReplicaEvent(
        time_s=timestamp,
        node=node,
        event=event_name,
        replica_size=_optional_nonnegative_int(
            clean.get("replica_size"), "replica_size", source, line_number
        ),
        mutation_sequence=(
            mutation_sequence
            if mutation_sequence is not None
            else replica_version
        ),
        replica_version=replica_version,
        cell_ids_added=_parse_cell_ids(
            clean.get("cell_ids_added"), source, line_number
        ),
        serialized_state_size=_optional_nonnegative_int(
            clean.get("serialized_state_size"),
            "serialized_state_size",
            source,
            line_number,
        ),
        time_base=time_base,
        source=str(source),
        line_number=line_number,
        fields=clean,
    )


def read_event_log(path, node=None, default_time_base="unix"):
    # type: (Union[str, Path], Optional[str], str) -> List[ReplicaEvent]
    """Read C++ ``timestamp, key=value`` logs or equivalent JSON Lines."""
    event_path = Path(path)
    fallback_node = str(node) if node is not None else _infer_node_from_path(event_path)
    default_time_base = _canonical_time_base(default_time_base)
    events = []  # type: List[ReplicaEvent]
    try:
        stream = event_path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        raise TraceFormatError("cannot open event log {}: {}".format(event_path, exc))

    with stream:
        for line_number, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("{"):
                try:
                    values = json.loads(stripped)
                except (TypeError, ValueError) as exc:
                    raise TraceFormatError(
                        "{}:{}: invalid JSON event: {}".format(
                            event_path, line_number, exc
                        )
                    )
                if not isinstance(values, dict):
                    raise TraceFormatError(
                        "{}:{}: JSON event must be an object".format(
                            event_path, line_number
                        )
                    )
                events.append(
                    _event_from_mapping(
                        values,
                        event_path,
                        line_number,
                        fallback_node,
                        default_time_base,
                    )
                )
                continue

            try:
                tokens = next(csv.reader([line], skipinitialspace=True))
            except csv.Error as exc:
                raise TraceFormatError(
                    "{}:{}: invalid CSV event: {}".format(
                        event_path, line_number, exc
                    )
                )
            tokens = [token.strip() for token in tokens]
            if tokens and tokens[0].lower() in (
                "timestamp",
                "timestamp_s",
                "timestamp_unix_s",
                "time_s",
                "elapsed_s",
            ):
                # A human-friendly header is optional for key/value logs.
                continue
            positional_time = None  # type: Optional[str]
            values = {}  # type: Dict[str, str]
            for index, token in enumerate(tokens):
                if not token:
                    continue
                if "=" not in token:
                    if index == 0:
                        positional_time = token
                        continue
                    raise TraceFormatError(
                        "{}:{}: expected key=value, got {!r}".format(
                            event_path, line_number, token
                        )
                    )
                key, value = token.split("=", 1)
                key = key.strip()
                if not key:
                    raise TraceFormatError(
                        "{}:{}: empty event key".format(event_path, line_number)
                    )
                if key in values:
                    raise TraceFormatError(
                        "{}:{}: duplicate event key {!r}".format(
                            event_path, line_number, key
                        )
                    )
                values[key] = value.strip()
            events.append(
                _event_from_mapping(
                    values,
                    event_path,
                    line_number,
                    fallback_node,
                    default_time_base,
                    positional_time=positional_time,
                )
            )
    return events


def read_event_logs(paths, default_time_base="unix"):
    # type: (Iterable[Union[str, Path]], str) -> List[ReplicaEvent]
    events = []  # type: List[ReplicaEvent]
    for path in paths:
        events.extend(read_event_log(path, default_time_base=default_time_base))
    return events


def event_elapsed_time(event, trace_epoch_s=None):
    # type: (ReplicaEvent, Optional[float]) -> float
    """Convert an event timestamp to trace-relative seconds.

    ``trace_epoch_s`` must be expressed in the same absolute clock as the
    event.  It is never inferred from the first event because application
    startup is not the mobility replay epoch.
    """
    try:
        event_time_is_finite = math.isfinite(event.time_s)
    except (TypeError, ValueError, OverflowError):
        event_time_is_finite = False
    if not event_time_is_finite:
        raise TimeAlignmentError("event timestamp must be finite")
    if event.time_base == "elapsed":
        return event.time_s
    if event.time_base in ("monotonic", "unix"):
        if trace_epoch_s is None:
            raise TimeAlignmentError(
                "event timestamps use {} time; trace_epoch_s is required".format(
                    event.time_base
                )
            )
        if not math.isfinite(trace_epoch_s):
            raise TimeAlignmentError("trace_epoch_s must be finite")
        elapsed = event.time_s - trace_epoch_s
        if not math.isfinite(elapsed):
            raise TimeAlignmentError("aligned event timestamp must be finite")
        return elapsed
    raise TimeAlignmentError("unsupported event time base: {}".format(event.time_base))


@dataclass(frozen=True)
class ReplicaTimeline:
    node: str
    times_s: Tuple[float, ...]
    sizes: Tuple[int, ...]

    def size_at(self, time_s):
        # type: (float) -> int
        index = bisect.bisect_right(self.times_s, time_s) - 1
        return 0 if index < 0 else self.sizes[index]


def build_replica_timelines(
    events,
    cell_count,
    trace_epoch_s=None,
    expected_nodes=None,
):
    # type: (Iterable[ReplicaEvent], int, Optional[float], Optional[Iterable[str]]) -> Tuple[Dict[str, ReplicaTimeline], Tuple[ValidationIssue, ...]]
    """Build step functions :math:`|S_i(t)|` and report log invariants.

    Replica state is reconstructed only from successful ``local_coverage``
    and ``remote_merge`` mutations, by monotonically unioning their
    ``cell_ids_added``. Reported sizes on triggers/receives are observations,
    not state transitions, and therefore cannot move the timeline backwards.

    New logs carry a per-node ``mutation_sequence`` captured atomically with
    the mutation. It defines mutation order even when concurrent callbacks
    write their log lines out of order. Legacy logs have no sequence and are
    ordered by timestamp; their reported sizes are not used to validate the
    union because those sizes and timestamps were captured after unlocking.
    """
    if cell_count <= 0 or cell_count > CELL_ID_CAPACITY:
        raise ValueError("cell_count must fit a non-empty uint16_t grid")

    grouped = {}  # type: Dict[str, List[Tuple[float, int, ReplicaEvent]]]
    materialized = list(events)
    for order, event in enumerate(materialized):
        if not isinstance(event, ReplicaEvent):
            raise TypeError("events must contain ReplicaEvent instances")
        elapsed = event_elapsed_time(event, trace_epoch_s)
        grouped.setdefault(event.node, []).append((elapsed, order, event))
    expected_node_set = None  # type: Optional[Set[str]]
    if expected_nodes is not None:
        expected_node_set = {str(node) for node in expected_nodes}
        for node in expected_node_set:
            grouped.setdefault(str(node), [])

    timelines = {}  # type: Dict[str, ReplicaTimeline]
    issues = []  # type: List[ValidationIssue]
    if expected_node_set is not None:
        for node in sorted(set(grouped).difference(expected_node_set)):
            issues.append(
                ValidationIssue(
                    "unexpected_node",
                    "event log contains node {} absent from mobility traces".format(node),
                    node=node,
                )
            )
    mutation_events = frozenset(("local_coverage", "remote_merge"))

    for node, entries in sorted(grouped.items()):
        # Validate auxiliary observations independently, but never feed their
        # potentially stale replica_size into the state timeline.
        for elapsed, _, event in entries:
            size = event.replica_size
            if size is not None and size > cell_count:
                issues.append(
                    ValidationIssue(
                        "replica_exceeds_grid",
                        "node {} reports replica_size {} for a {}-cell grid".format(
                            node, size, cell_count
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )
            if (
                event.event == "dissemination_trigger"
                and event.serialized_state_size is not None
                and size is not None
                and event.serialized_state_size != 2 * size
            ):
                issues.append(
                    ValidationIssue(
                        "serialized_state_size_mismatch",
                        "node {} trigger serialized {} bytes for {} uint16 cells".format(
                            node, event.serialized_state_size, size
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )

        mutations = [
            item for item in entries if item[2].event in mutation_events
        ]
        sequence_values = []  # type: List[Optional[int]]
        sequence_conflict = False
        for _, _, event in mutations:
            sequence = event.mutation_sequence
            if event.replica_version is not None:
                if sequence is not None and sequence != event.replica_version:
                    sequence_conflict = True
                    issues.append(
                        ValidationIssue(
                            "conflicting_mutation_sequence",
                            "node {} mutation_sequence {} disagrees with "
                            "replica_version {}".format(
                                node, sequence, event.replica_version
                            ),
                            node=node,
                            source=event.source,
                        )
                    )
                elif sequence is None:
                    sequence = event.replica_version
            sequence_values.append(sequence)

        has_sequence = any(value is not None for value in sequence_values)
        all_sequenced = bool(mutations) and all(
            value is not None for value in sequence_values
        )
        if has_sequence and not all_sequenced:
            issues.append(
                ValidationIssue(
                    "mixed_mutation_sequence",
                    "node {} mixes sequenced and legacy mutation events".format(node),
                    node=node,
                )
            )

        if all_sequenced and not sequence_conflict:
            mutations_with_sequence = list(zip(mutations, sequence_values))
            mutations_with_sequence.sort(key=lambda item: (item[1], item[0][1]))
            mutations = [item[0] for item in mutations_with_sequence]
            sorted_sequences = [item[1] for item in mutations_with_sequence]
            expected_sequences = list(range(1, len(sorted_sequences) + 1))
            if sorted_sequences != expected_sequences:
                event = mutations[0][2]
                issues.append(
                    ValidationIssue(
                        "invalid_mutation_sequence",
                        "node {} mutation_sequence values must be contiguous "
                        "from 1; observed {}".format(node, sorted_sequences),
                        node=node,
                        source=event.source,
                    )
                )
            previous_mutation_time = None  # type: Optional[float]
            for elapsed, _, event in mutations:
                if (
                    previous_mutation_time is not None
                    and elapsed < previous_mutation_time
                ):
                    issues.append(
                        ValidationIssue(
                            "mutation_sequence_time_mismatch",
                            "node {} mutation timestamp moved backwards in "
                            "mutation_sequence order".format(node),
                            node=node,
                            time_s=elapsed,
                            source=event.source,
                        )
                    )
                previous_mutation_time = (
                    elapsed
                    if previous_mutation_time is None
                    else max(previous_mutation_time, elapsed)
                )
        else:
            # For legacy or malformed mixed logs, timestamp is the only safe
            # ordering key. Input order breaks equal-timestamp ties.
            mutations.sort(key=lambda item: (item[0], item[1]))

        times = []  # type: List[float]
        sizes = []  # type: List[int]
        known_cells = set()  # type: Set[int]
        previous_timeline_time = None  # type: Optional[float]
        for elapsed, _, event in mutations:
            before_size = len(known_cells)
            if not event.cell_ids_added:
                issues.append(
                    ValidationIssue(
                        "missing_added_cells",
                        "{} for node {} omits cell_ids_added".format(
                            event.event, node
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )
            for cell_id in event.cell_ids_added:
                if cell_id >= cell_count:
                    issues.append(
                        ValidationIssue(
                            "event_cell_outside_grid",
                            "node {} logged cell {} for a {}-cell grid".format(
                                node, cell_id, cell_count
                            ),
                            node=node,
                            time_s=elapsed,
                            source=event.source,
                        )
                    )
                    continue
                if cell_id in known_cells:
                    issues.append(
                        ValidationIssue(
                            "cell_added_twice",
                            "node {} logged cell {} as newly added more than once".format(
                                node, cell_id
                            ),
                            node=node,
                            time_s=elapsed,
                            source=event.source,
                        )
                    )
                    continue
                known_cells.add(cell_id)

            derived_size = len(known_cells)
            if derived_size == before_size:
                issues.append(
                    ValidationIssue(
                        "mutation_without_growth",
                        "{} for node {} did not add a new valid cell".format(
                            event.event, node
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )
            if all_sequenced and not sequence_conflict and event.replica_size is None:
                issues.append(
                    ValidationIssue(
                        "missing_replica_size",
                        "sequenced mutation for node {} omits replica_size".format(
                            node
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )
            elif (
                all_sequenced
                and not sequence_conflict
                and event.replica_size != derived_size
            ):
                issues.append(
                    ValidationIssue(
                        "replica_size_union_mismatch",
                        "node {} reports replica_size {} at mutation_sequence {} "
                        "but cell_ids_added imply {}".format(
                            node,
                            event.replica_size,
                            event.mutation_sequence,
                            derived_size,
                        ),
                        node=node,
                        time_s=elapsed,
                        source=event.source,
                    )
                )

            timeline_time = elapsed
            if (
                previous_timeline_time is not None
                and timeline_time < previous_timeline_time
            ):
                # The issue above makes this analysis invalid, but keeping a
                # sorted step function avoids silently breaking bisect_right.
                timeline_time = previous_timeline_time
            times.append(timeline_time)
            sizes.append(derived_size)
            previous_timeline_time = timeline_time
        timelines[node] = ReplicaTimeline(node, tuple(times), tuple(sizes))
        if not times:
            issues.append(
                ValidationIssue(
                    "no_replica_observations",
                    "node {} has no local_coverage or remote_merge mutation".format(
                        node
                    ),
                    node=node,
                )
            )
    return timelines, tuple(issues)


@dataclass(frozen=True)
class CarSample:
    time_s: float
    ground_truth_size: int
    per_node_replica_size: Mapping[str, int]
    per_node_car: Mapping[str, float]
    swarm_car: float

    def to_dict(self):
        # type: () -> Dict[str, Any]
        def json_number(value):
            return None if isinstance(value, float) and math.isnan(value) else value

        return {
            "time_s": self.time_s,
            "ground_truth_size": self.ground_truth_size,
            "per_node_replica_size": dict(self.per_node_replica_size),
            "per_node_car": {
                node: json_number(value) for node, value in self.per_node_car.items()
            },
            "swarm_car": json_number(self.swarm_car),
        }


def compute_car_samples(ground_truth, timelines, checkpoint_times_s):
    # type: (GroundTruthTimeline, Mapping[str, ReplicaTimeline], Iterable[float]) -> Tuple[Tuple[CarSample, ...], Tuple[ValidationIssue, ...]]
    """Compute node and swarm CAR without clamping impossible values."""
    samples = []  # type: List[CarSample]
    issues = []  # type: List[ValidationIssue]
    nodes = sorted(timelines)
    if not nodes:
        issues.append(ValidationIssue("no_nodes", "no replica event logs were supplied"))

    for checkpoint in checkpoint_times_s:
        checkpoint = float(checkpoint)
        if not math.isfinite(checkpoint):
            raise ValueError("CAR checkpoint times must be finite")
        truth_size = ground_truth.size_at(checkpoint)
        replica_sizes = {
            node: timelines[node].size_at(checkpoint) for node in nodes
        }
        if truth_size == 0:
            node_car = {node: float("nan") for node in nodes}
            swarm_car = float("nan")
            for node, size in replica_sizes.items():
                if size > 0:
                    issues.append(
                        ValidationIssue(
                            "replica_before_physical_coverage",
                            "node {} knows {} cells while |G(t)| is zero".format(
                                node, size
                            ),
                            node=node,
                            time_s=checkpoint,
                        )
                    )
        else:
            node_car = {
                node: replica_sizes[node] / float(truth_size) for node in nodes
            }
            for node, value in node_car.items():
                if value > 1.0:
                    issues.append(
                        ValidationIssue(
                            "car_exceeds_one",
                            "CAR for node {} is {} at t={} (|S|={}, |G|={})".format(
                                node,
                                value,
                                checkpoint,
                                replica_sizes[node],
                                truth_size,
                            ),
                            node=node,
                            time_s=checkpoint,
                        )
                    )
            swarm_car = (
                sum(node_car.values()) / float(len(node_car))
                if node_car
                else float("nan")
            )
        samples.append(
            CarSample(
                time_s=checkpoint,
                ground_truth_size=truth_size,
                per_node_replica_size=replica_sizes,
                per_node_car=node_car,
                swarm_car=swarm_car,
            )
        )
    return tuple(samples), tuple(issues)


@dataclass(frozen=True)
class CarAnalysis:
    t_cover_s: Optional[float]
    checkpoint_offsets_s: Tuple[float, ...]
    samples: Tuple[CarSample, ...]
    issues: Tuple[ValidationIssue, ...]

    @property
    def valid(self):
        # type: () -> bool
        return not self.issues

    def to_dict(self):
        # type: () -> Dict[str, Any]
        return {
            "valid": self.valid,
            "t_cover_s": self.t_cover_s,
            "checkpoint_offsets_s": list(self.checkpoint_offsets_s),
            "samples": [sample.to_dict() for sample in self.samples],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def analyze_car(
    ground_truth,
    events,
    checkpoint_offsets_s=DEFAULT_CHECKPOINT_OFFSETS_S,
    trace_epoch_s=None,
    expected_nodes=None,
):
    # type: (GroundTruthTimeline, Iterable[ReplicaEvent], Iterable[float], Optional[float], Optional[Iterable[str]]) -> CarAnalysis
    offsets = tuple(float(value) for value in checkpoint_offsets_s)
    if any(not math.isfinite(value) or value < 0.0 for value in offsets):
        raise ValueError("checkpoint offsets must be finite and >= 0")
    if ground_truth.t_cover is None:
        return CarAnalysis(
            t_cover_s=None,
            checkpoint_offsets_s=offsets,
            samples=(),
            issues=(
                ValidationIssue(
                    "incomplete_ground_truth",
                    "T_cover is undefined because physical coverage is incomplete",
                ),
            ),
        )

    timelines, timeline_issues = build_replica_timelines(
        events,
        ground_truth.grid.cell_count,
        trace_epoch_s=trace_epoch_s,
        expected_nodes=expected_nodes,
    )
    times = tuple(ground_truth.t_cover + offset for offset in offsets)
    samples, car_issues = compute_car_samples(ground_truth, timelines, times)
    return CarAnalysis(
        t_cover_s=ground_truth.t_cover,
        checkpoint_offsets_s=offsets,
        samples=samples,
        issues=tuple(timeline_issues) + tuple(car_issues),
    )


def _add_grid_arguments(parser, required=True):
    # type: (argparse.ArgumentParser, bool) -> None
    parser.add_argument("--origin-x-m", type=float, default=0.0)
    parser.add_argument("--origin-y-m", type=float, default=0.0)
    parser.add_argument("--width-m", type=float, required=required)
    parser.add_argument("--height-m", type=float, required=required)
    parser.add_argument("--rows", type=int, required=required)
    parser.add_argument("--cols", type=int, required=required)


def _grid_from_args(args, config_path=None):
    # type: (argparse.Namespace, Optional[Union[str, Path]]) -> GridSpec
    explicit = (args.width_m, args.height_m, args.rows, args.cols)
    if all(value is not None for value in explicit):
        return GridSpec(
            origin_x_m=args.origin_x_m,
            origin_y_m=args.origin_y_m,
            width_m=args.width_m,
            height_m=args.height_m,
            rows=args.rows,
            cols=args.cols,
        )
    if any(value is not None for value in explicit):
        raise ValueError("width, height, rows and cols must be supplied together")
    if config_path is None:
        raise ValueError(
            "grid arguments are required unless --grid-config or --run-dir supplies node_config.json"
        )
    path = Path(config_path)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TraceFormatError("cannot read grid config {}: {}".format(path, exc))
    grid_values = values.get("grid") if isinstance(values, dict) else None
    if not isinstance(grid_values, dict):
        raise TraceFormatError("{} does not contain a grid object".format(path))
    required_keys = (
        "origin_x_m",
        "origin_y_m",
        "width_m",
        "height_m",
        "rows",
        "cols",
    )
    missing = [key for key in required_keys if key not in grid_values]
    if missing:
        raise TraceFormatError(
            "{} grid is missing: {}".format(path, ", ".join(missing))
        )
    return GridSpec(**{key: grid_values[key] for key in required_keys})


def _checkpoint_offsets(value):
    # type: (str) -> Tuple[float, ...]
    try:
        offsets = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("checkpoints must be comma-separated seconds")
    if not offsets or any(not math.isfinite(item) or item < 0.0 for item in offsets):
        raise argparse.ArgumentTypeError(
            "checkpoints must be non-empty, finite and non-negative"
        )
    return offsets


def _write_json(payload, output):
    # type: (Mapping[str, Any], Optional[str]) -> None
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if output:
        Path(output).write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized)


def _command_check(args):
    # type: (argparse.Namespace) -> int
    grid = _grid_from_args(args, args.grid_config)
    traces = read_traces(args.traces)
    report = check_traces(
        traces,
        grid,
        require_full_coverage=not args.allow_incomplete,
        post_coverage_window_s=args.post_coverage_window_s,
        require_start_at_zero=not args.allow_nonzero_start,
        require_motion_after_cover=args.require_motion_after_cover,
        coverage_start_time_s=args.coverage_start_time_s,
    )
    _write_json(report.to_dict(), args.output)
    return 0 if report.valid else 2


def _command_analyze(args):
    # type: (argparse.Namespace) -> int
    trace_paths = list(args.traces or [])
    event_paths = list(args.event_logs or [])
    clock_path = args.experiment_clock
    grid_config_path = args.grid_config
    if clock_path == "auto" and not args.run_dir:
        raise TraceFormatError("--experiment-clock auto requires --run-dir")
    if args.run_dir:
        run_dir = Path(args.run_dir)
        if not trace_paths:
            trace_paths = [str(path) for path in sorted(
                (run_dir / "mobility_traces").glob("node_*.csv")
            )]
        if not event_paths:
            event_paths = [str(path) for path in sorted(run_dir.glob("node_*.log.events"))]
        if clock_path is None or clock_path == "auto":
            candidate = run_dir / "experiment_clock.json"
            if candidate.is_file():
                clock_path = str(candidate)
            elif clock_path == "auto":
                raise TraceFormatError(
                    "{} does not contain experiment_clock.json".format(run_dir)
                )
        if grid_config_path is None:
            candidate = run_dir / "node_config.json"
            if candidate.is_file():
                grid_config_path = str(candidate)
    if not trace_paths:
        raise TraceFormatError(
            "no traces found; use --trace or --run-dir with mobility_traces/node_*.csv"
        )
    if not event_paths:
        raise TraceFormatError(
            "no event logs found; use --event-log or --run-dir with node_*.log.events"
        )

    grid = _grid_from_args(args, grid_config_path)

    clock = read_experiment_clock(clock_path) if clock_path else None
    trace_epoch_s = args.trace_epoch_s
    coverage_start_time_s = args.coverage_start_time_s
    if clock is not None:
        if trace_epoch_s is not None and not math.isclose(
            trace_epoch_s, clock.trace_start_unix_s, rel_tol=0.0, abs_tol=1e-6
        ):
            raise TimeAlignmentError(
                "--trace-epoch-s disagrees with experiment_clock.json"
            )
        trace_epoch_s = clock.trace_start_unix_s
        clock_coverage_start = clock.coverage_start_trace_s
        if coverage_start_time_s is not None and not math.isclose(
            coverage_start_time_s,
            clock_coverage_start,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            raise TimeAlignmentError(
                "--coverage-start-time-s disagrees with experiment_clock.json"
            )
        coverage_start_time_s = clock_coverage_start

    traces = read_traces(trace_paths)
    trace_report = check_traces(
        traces,
        grid,
        require_full_coverage=True,
        post_coverage_window_s=max(args.checkpoints),
        require_start_at_zero=not args.allow_nonzero_start,
        coverage_start_time_s=coverage_start_time_s,
    )
    if trace_report.covered_cell_count:
        ground_truth = build_ground_truth(
            traces, grid, coverage_start_time_s=coverage_start_time_s
        )
        events = read_event_logs(
            event_paths, default_time_base=args.event_time_base
        )
        analysis = analyze_car(
            ground_truth,
            events,
            checkpoint_offsets_s=args.checkpoints,
            trace_epoch_s=trace_epoch_s,
            expected_nodes=sorted(traces),
        )
    else:
        analysis = CarAnalysis(
            None,
            args.checkpoints,
            (),
            (ValidationIssue("no_ground_truth", "no valid ground truth was produced"),),
        )
    payload = {
        "experiment_clock": None if clock is None else clock.to_dict(),
        "trace_validation": trace_report.to_dict(),
        "car": analysis.to_dict(),
    }
    _write_json(payload, args.output)
    return 0 if trace_report.valid and analysis.valid else 2


def build_argument_parser():
    # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser(
        description="Validate spatial mobility traces and compute Coverage Awareness Ratio"
    )
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser(
        "check-traces", aliases=["check"], help="validate mobility traces"
    )
    _add_grid_arguments(check, required=False)
    check.add_argument(
        "--grid-config",
        help="node_config.json containing the canonical GridSpec",
    )
    check.add_argument(
        "--post-coverage-window-s",
        type=float,
        default=DEFAULT_POST_COVERAGE_WINDOW_S,
    )
    check.add_argument("--allow-incomplete", action="store_true")
    check.add_argument("--allow-nonzero-start", action="store_true")
    check.add_argument("--require-motion-after-cover", action="store_true")
    check.add_argument(
        "--coverage-start-time-s",
        type=float,
        help="ignore physical coverage before this trace-relative time",
    )
    check.add_argument("--output")
    check.add_argument("traces", nargs="+")
    check.set_defaults(handler=_command_check)

    analyze = subparsers.add_parser("analyze", help="compute CAR checkpoints")
    _add_grid_arguments(analyze, required=False)
    analyze.add_argument(
        "--run-dir",
        help="auto-discover traces, event logs and experiment_clock.json",
    )
    analyze.add_argument(
        "--grid-config",
        help="node_config.json containing GridSpec (auto-discovered with --run-dir)",
    )
    analyze.add_argument("--trace", dest="traces", action="append")
    analyze.add_argument(
        "--event-log", dest="event_logs", action="append"
    )
    analyze.add_argument(
        "--event-time-base",
        choices=("elapsed", "monotonic", "unix"),
        default="unix",
        help="clock used by positional/key-value event timestamps",
    )
    analyze.add_argument(
        "--trace-epoch-s",
        type=float,
        help="replay epoch in the absolute event clock (required unless elapsed)",
    )
    analyze.add_argument(
        "--coverage-start-time-s",
        type=float,
        help="coverage start in trace-relative seconds",
    )
    analyze.add_argument(
        "--experiment-clock",
        help="JSON containing trace_start_unix_s and coverage_start_unix_s",
    )
    analyze.add_argument(
        "--checkpoints",
        type=_checkpoint_offsets,
        default=DEFAULT_CHECKPOINT_OFFSETS_S,
        help="seconds after T_cover (default: 0,5,10,15,20)",
    )
    analyze.add_argument("--allow-nonzero-start", action="store_true")
    analyze.add_argument("--output")
    analyze.set_defaults(handler=_command_analyze)
    return parser


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help(sys.stderr)
        return 2
    try:
        return int(args.handler(args))
    except (
        TraceFormatError,
        TraceValidationError,
        TimeAlignmentError,
        ValueError,
        OSError,
    ) as exc:
        payload = {
            "valid": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        try:
            _write_json(payload, getattr(args, "output", None))
        except OSError:
            sys.stderr.write(json.dumps(payload, sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
