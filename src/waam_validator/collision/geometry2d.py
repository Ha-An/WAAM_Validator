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


def _orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _orientation_tolerance(ax: float, ay: float, bx: float, by: float, epsilon: float) -> float:
    """Convert the configured linear tolerance to cross-product area units."""
    return epsilon * max(math.hypot(bx - ax, by - ay), epsilon)


def _on_segment(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    px: float,
    py: float,
    epsilon: float,
) -> bool:
    length = math.hypot(bx - ax, by - ay)
    if length <= epsilon:
        return math.hypot(px - ax, py - ay) <= epsilon
    return (
        abs(_orient(ax, ay, bx, by, px, py)) <= epsilon * length
        and min(ax, bx) - epsilon <= px <= max(ax, bx) + epsilon
        and min(ay, by) - epsilon <= py <= max(ay, by) + epsilon
    )


def _proper_intersection_point(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> tuple[float, float]:
    direction_ax = bx - ax
    direction_ay = by - ay
    direction_bx = dx - cx
    direction_by = dy - cy
    denominator = direction_ax * direction_by - direction_ay * direction_bx
    t_value = ((cx - ax) * direction_by - (cy - ay) * direction_bx) / denominator
    return ax + t_value * direction_ax, ay + t_value * direction_ay


def _collinear_overlap_midpoint(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
    epsilon: float,
) -> tuple[bool, bool, tuple[float, float] | None]:
    direction_x = bx - ax
    direction_y = by - ay
    length = math.hypot(direction_x, direction_y)
    if length <= epsilon:
        direction_x = dx - cx
        direction_y = dy - cy
        length = math.hypot(direction_x, direction_y)
        if length <= epsilon:
            distance = math.hypot(ax - cx, ay - cy)
            return distance <= epsilon, False, (ax, ay)
        start_x, start_y, end_x, end_y = cx, cy, dx, dy
        other_start_x, other_start_y, other_end_x, other_end_y = ax, ay, bx, by
    else:
        start_x, start_y, end_x, end_y = ax, ay, bx, by
        other_start_x, other_start_y, other_end_x, other_end_y = cx, cy, dx, dy
    unit_x = direction_x / length
    unit_y = direction_y / length

    def project(px: float, py: float) -> float:
        return px * unit_x + py * unit_y

    start_projection = project(start_x, start_y)
    end_projection = project(end_x, end_y)
    other_start_projection = project(other_start_x, other_start_y)
    other_end_projection = project(other_end_x, other_end_y)
    low = max(
        min(start_projection, end_projection),
        min(other_start_projection, other_end_projection),
    )
    high = min(
        max(start_projection, end_projection),
        max(other_start_projection, other_end_projection),
    )
    if high < low - epsilon:
        return False, False, None
    midpoint_projection = (low + high) / 2.0
    offset = midpoint_projection - start_projection
    point = (
        start_x + offset * unit_x,
        start_y + offset * unit_y,
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
    ax, ay = _point2(base_a_xy)
    bx, by = _point2(tcp_a_xy)
    cx, cy = _point2(base_b_xy)
    dx, dy = _point2(tcp_b_xy)
    o1 = _orient(ax, ay, bx, by, cx, cy)
    o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay)
    o4 = _orient(cx, cy, dx, dy, bx, by)
    tolerance_ab = _orientation_tolerance(ax, ay, bx, by, epsilon_mm)
    tolerance_cd = _orientation_tolerance(cx, cy, dx, dy, epsilon_mm)
    proper = (
        (o1 > tolerance_ab and o2 < -tolerance_ab) or (o1 < -tolerance_ab and o2 > tolerance_ab)
    ) and ((o3 > tolerance_cd and o4 < -tolerance_cd) or (o3 < -tolerance_cd and o4 > tolerance_cd))
    if proper:
        x_mm, y_mm = _proper_intersection_point(ax, ay, bx, by, cx, cy, dx, dy)
        return SegmentIntersectionResult(True, x_mm, y_mm)

    collinear = (
        abs(o1) <= tolerance_ab
        and abs(o2) <= tolerance_ab
        and abs(o3) <= tolerance_cd
        and abs(o4) <= tolerance_cd
    )
    if collinear:
        overlaps, positive_length, overlap_point = _collinear_overlap_midpoint(
            ax, ay, bx, by, cx, cy, dx, dy, epsilon_mm
        )
        collision = overlaps and (positive_length or touching_is_collision)
        if collision and overlap_point is not None:
            return SegmentIntersectionResult(True, overlap_point[0], overlap_point[1])
        return SegmentIntersectionResult(False)

    if touching_is_collision:
        if _on_segment(ax, ay, bx, by, cx, cy, epsilon_mm):
            return SegmentIntersectionResult(True, cx, cy)
        if _on_segment(ax, ay, bx, by, dx, dy, epsilon_mm):
            return SegmentIntersectionResult(True, dx, dy)
        if _on_segment(cx, cy, dx, dy, ax, ay, epsilon_mm):
            return SegmentIntersectionResult(True, ax, ay)
        if _on_segment(cx, cy, dx, dy, bx, by, epsilon_mm):
            return SegmentIntersectionResult(True, bx, by)
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


def check_arm_crossing_xy_batch(
    base_a_xy: np.ndarray,
    tcp_a_xy: np.ndarray,
    base_b_xy: np.ndarray,
    tcp_b_xy: np.ndarray,
    epsilon_mm: float,
    touching_is_collision: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized arm crossing check for a batch of TCP positions.

    The returned coordinate arrays contain NaN for non-colliding samples.  Keeping
    this hot path inside NumPy avoids creating millions of temporary Python result
    objects during long validations.
    """
    base_a = np.asarray(base_a_xy, dtype=np.float64)
    base_b = np.asarray(base_b_xy, dtype=np.float64)
    tcp_a = np.asarray(tcp_a_xy, dtype=np.float64)
    tcp_b = np.asarray(tcp_b_xy, dtype=np.float64)
    count = len(tcp_a)
    collision = np.zeros(count, dtype=np.bool_)
    crossing_x = np.full(count, np.nan, dtype=np.float64)
    crossing_y = np.full(count, np.nan, dtype=np.float64)
    if count == 0:
        return collision, crossing_x, crossing_y

    ax, ay = float(base_a[0]), float(base_a[1])
    cx, cy = float(base_b[0]), float(base_b[1])
    bx, by = tcp_a[:, 0], tcp_a[:, 1]
    dx, dy = tcp_b[:, 0], tcp_b[:, 1]

    direction_ab_x = bx - ax
    direction_ab_y = by - ay
    direction_cd_x = dx - cx
    direction_cd_y = dy - cy
    length_ab = np.hypot(direction_ab_x, direction_ab_y)
    length_cd = np.hypot(direction_cd_x, direction_cd_y)
    tolerance_ab = epsilon_mm * np.maximum(length_ab, epsilon_mm)
    tolerance_cd = epsilon_mm * np.maximum(length_cd, epsilon_mm)

    o1 = direction_ab_x * (cy - ay) - direction_ab_y * (cx - ax)
    o2 = direction_ab_x * (dy - ay) - direction_ab_y * (dx - ax)
    o3 = direction_cd_x * (ay - cy) - direction_cd_y * (ax - cx)
    o4 = direction_cd_x * (by - cy) - direction_cd_y * (bx - cx)
    proper = (
        ((o1 > tolerance_ab) & (o2 < -tolerance_ab)) | ((o1 < -tolerance_ab) & (o2 > tolerance_ab))
    ) & (
        ((o3 > tolerance_cd) & (o4 < -tolerance_cd)) | ((o3 < -tolerance_cd) & (o4 > tolerance_cd))
    )
    collision[proper] = True
    denominator = direction_ab_x * direction_cd_y - direction_ab_y * direction_cd_x
    proper_indices = np.flatnonzero(proper)
    if len(proper_indices):
        t_value = (
            (cx - ax) * direction_cd_y[proper_indices] - (cy - ay) * direction_cd_x[proper_indices]
        ) / denominator[proper_indices]
        crossing_x[proper_indices] = ax + t_value * direction_ab_x[proper_indices]
        crossing_y[proper_indices] = ay + t_value * direction_ab_y[proper_indices]

    collinear = (
        (np.abs(o1) <= tolerance_ab)
        & (np.abs(o2) <= tolerance_ab)
        & (np.abs(o3) <= tolerance_cd)
        & (np.abs(o4) <= tolerance_cd)
    )
    for index in np.flatnonzero(collinear):
        result = check_arm_crossing_xy(
            base_a,
            tcp_a[index],
            base_b,
            tcp_b[index],
            epsilon_mm,
            touching_is_collision,
        )
        if result.intersects:
            collision[index] = True
            crossing_x[index] = result.x_mm
            crossing_y[index] = result.y_mm

    if touching_is_collision:
        remaining = ~(proper | collinear)
        bounds_ab_x = (np.minimum(ax, bx) - epsilon_mm <= cx) & (
            cx <= np.maximum(ax, bx) + epsilon_mm
        )
        bounds_ab_y = (np.minimum(ay, by) - epsilon_mm <= cy) & (
            cy <= np.maximum(ay, by) + epsilon_mm
        )
        c_on_ab = (
            (
                np.where(length_ab <= epsilon_mm, np.hypot(cx - ax, cy - ay), np.abs(o1))
                <= np.where(length_ab <= epsilon_mm, epsilon_mm, epsilon_mm * length_ab)
            )
            & bounds_ab_x
            & bounds_ab_y
        )
        bounds_d_ab = (
            (np.minimum(ax, bx) - epsilon_mm <= dx)
            & (dx <= np.maximum(ax, bx) + epsilon_mm)
            & (np.minimum(ay, by) - epsilon_mm <= dy)
            & (dy <= np.maximum(ay, by) + epsilon_mm)
        )
        d_on_ab = (np.abs(o2) <= epsilon_mm * np.maximum(length_ab, epsilon_mm)) & bounds_d_ab
        bounds_a_cd = (
            (np.minimum(cx, dx) - epsilon_mm <= ax)
            & (ax <= np.maximum(cx, dx) + epsilon_mm)
            & (np.minimum(cy, dy) - epsilon_mm <= ay)
            & (ay <= np.maximum(cy, dy) + epsilon_mm)
        )
        a_on_cd = (np.abs(o3) <= epsilon_mm * np.maximum(length_cd, epsilon_mm)) & bounds_a_cd
        bounds_b_cd = (
            (np.minimum(cx, dx) - epsilon_mm <= bx)
            & (bx <= np.maximum(cx, dx) + epsilon_mm)
            & (np.minimum(cy, dy) - epsilon_mm <= by)
            & (by <= np.maximum(cy, dy) + epsilon_mm)
        )
        b_on_cd = (np.abs(o4) <= epsilon_mm * np.maximum(length_cd, epsilon_mm)) & bounds_b_cd

        def apply_touch(
            touches: np.ndarray,
            point_x: float | np.ndarray,
            point_y: float | np.ndarray,
        ) -> None:
            selected = remaining & touches & ~collision
            collision[selected] = True
            if isinstance(point_x, np.ndarray) and isinstance(point_y, np.ndarray):
                crossing_x[selected] = point_x[selected]
                crossing_y[selected] = point_y[selected]
            else:
                crossing_x[selected] = point_x
                crossing_y[selected] = point_y

        for touches, point_x, point_y in (
            (c_on_ab, cx, cy),
            (d_on_ab, dx, dy),
            (a_on_cd, ax, ay),
            (b_on_cd, bx, by),
        ):
            apply_touch(touches, point_x, point_y)
    return collision, crossing_x, crossing_y
