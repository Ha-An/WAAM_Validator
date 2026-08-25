"""Robust small-scale 2D collision primitives."""

from __future__ import annotations

import math

import numpy as np

from ..models import SegmentIntersectionResult, TcpRadiusResult

Point2D = tuple[float, float]


def _point2(value: np.ndarray) -> Point2D:
    """Copy a NumPy point into stable Python scalars at the API boundary."""
    point = np.asarray(value, dtype=np.float64)
    return float(point[0]), float(point[1])


def _orient(a: Point2D, b: Point2D, c: Point2D) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point2D, b: Point2D, point: Point2D, epsilon: float) -> bool:
    return (
        abs(_orient(a, b, point)) <= epsilon
        and min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
    )


def _proper_intersection_point(
    a: Point2D, b: Point2D, c: Point2D, d: Point2D
) -> tuple[float, float]:
    direction_a = (b[0] - a[0], b[1] - a[1])
    direction_b = (d[0] - c[0], d[1] - c[1])
    denominator = direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0]
    t_value = (
        (c[0] - a[0]) * direction_b[1] - (c[1] - a[1]) * direction_b[0]
    ) / denominator
    return a[0] + t_value * direction_a[0], a[1] + t_value * direction_a[1]


def _collinear_overlap_midpoint(
    a: Point2D,
    b: Point2D,
    c: Point2D,
    d: Point2D,
    epsilon: float,
) -> tuple[bool, bool, tuple[float, float] | None]:
    direction = (b[0] - a[0], b[1] - a[1])
    axis = 0 if abs(direction[0]) >= abs(direction[1]) else 1
    if abs(direction[axis]) <= epsilon:
        direction = (d[0] - c[0], d[1] - c[1])
        axis = 0 if abs(direction[0]) >= abs(direction[1]) else 1
        if abs(direction[axis]) <= epsilon:
            distance = math.hypot(a[0] - c[0], a[1] - c[1])
            return distance <= epsilon, False, a
        start, end = c, d
        other_start, other_end = a, b
    else:
        start, end = a, b
        other_start, other_end = c, d
    low = max(min(start[axis], end[axis]), min(other_start[axis], other_end[axis]))
    high = min(max(start[axis], end[axis]), max(other_start[axis], other_end[axis]))
    if high < low - epsilon:
        return False, False, None
    midpoint_axis = (low + high) / 2.0
    vector = (end[0] - start[0], end[1] - start[1])
    parameter = (midpoint_axis - start[axis]) / vector[axis]
    point = (
        start[0] + parameter * vector[0],
        start[1] + parameter * vector[1],
    )
    return True, high - low > epsilon, point


def check_arm_crossing_xy(
    base_a_xy: np.ndarray,
    tcp_a_xy: np.ndarray,
    base_b_xy: np.ndarray,
    tcp_b_xy: np.ndarray,
    epsilon_mm: float,
    touching_is_collision: bool,
) -> SegmentIntersectionResult:
    """Check proper, touching, and collinear intersection of two XY segments."""
    a = _point2(base_a_xy)
    b = _point2(tcp_a_xy)
    c = _point2(base_b_xy)
    d = _point2(tcp_b_xy)
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    proper = (
        ((o1 > epsilon_mm and o2 < -epsilon_mm) or (o1 < -epsilon_mm and o2 > epsilon_mm))
        and ((o3 > epsilon_mm and o4 < -epsilon_mm) or (o3 < -epsilon_mm and o4 > epsilon_mm))
    )
    if proper:
        x_mm, y_mm = _proper_intersection_point(a, b, c, d)
        return SegmentIntersectionResult(True, x_mm, y_mm)

    collinear = all(abs(value) <= epsilon_mm for value in (o1, o2, o3, o4))
    if collinear:
        overlaps, positive_length, overlap_point = _collinear_overlap_midpoint(
            a, b, c, d, epsilon_mm
        )
        collision = overlaps and (positive_length or touching_is_collision)
        if collision and overlap_point is not None:
            return SegmentIntersectionResult(True, overlap_point[0], overlap_point[1])
        return SegmentIntersectionResult(False)

    if touching_is_collision:
        for endpoint, start, end in ((c, a, b), (d, a, b), (a, c, d), (b, c, d)):
            if _on_segment(start, end, endpoint, epsilon_mm):
                return SegmentIntersectionResult(True, endpoint[0], endpoint[1])
    return SegmentIntersectionResult(False)


def check_tcp_radius_xy(
    tcp_a_xy: np.ndarray,
    radius_a_mm: float,
    tcp_b_xy: np.ndarray,
    radius_b_mm: float,
    touching_is_collision: bool,
) -> TcpRadiusResult:
    """Check whether two XY TCP clearance circles overlap or touch."""
    tcp_a = _point2(tcp_a_xy)
    tcp_b = _point2(tcp_b_xy)
    distance = math.hypot(tcp_a[0] - tcp_b[0], tcp_a[1] - tcp_b[1])
    required = radius_a_mm + radius_b_mm
    collision = distance <= required if touching_is_collision else distance < required
    return TcpRadiusResult(collision, distance, required)
