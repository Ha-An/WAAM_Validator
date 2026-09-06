from __future__ import annotations

import math
from pathlib import Path

import pytest
import trimesh
from shapely import Polygon
from shapely.geometry import GeometryCollection

from waam_validator.config.loader import load_config
from waam_validator.errors import ValidationMessages
from waam_validator.shape.deposition import build_deposited_layers
from waam_validator.shape.layer_index import determine_layer_index
from waam_validator.shape.metrics import _unit_interval, compute_shape_metrics
from waam_validator.shape.polygon_utils import polygon_components
from waam_validator.shape.target import (
    determine_evaluation_layers,
    load_target_mesh,
    slice_target_layers,
)
from waam_validator.trajectory.loader import load_trajectory_csv


def test_top_layer_index(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    assert determine_layer_index(2.0, config) == 0
    assert determine_layer_index(4.0, config) == 1
    assert determine_layer_index(6.0, config) == 2


def test_center_layer_index(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    center_process = config.process.model_copy(update={"tcp_z_reference": "center"})
    center_config = config.model_copy(update={"process": center_process})
    assert determine_layer_index(1.0, center_config) == 0
    assert determine_layer_index(3.0, center_config) == 1
    assert determine_layer_index(5.0, center_config) == 2


def test_nominal_bead_area(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    layers = build_deposited_layers(trajectories, config)
    expected = 80.0 * 4.0 + math.pi * 2.0**2
    assert layers[0].area == pytest.approx(expected, rel=2e-3)


def test_target_slice_and_exact_metrics(fixture_root: Path) -> None:
    job = fixture_root / "collision_free"
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    messages = ValidationMessages()
    mesh = load_target_mesh(job / "target.stl", config, messages)
    deposited = build_deposited_layers(trajectories, config)
    indices = determine_evaluation_layers(mesh, deposited, config)
    target = slice_target_layers(mesh, indices, config, messages)
    metrics, layers = compute_shape_metrics(
        deposited, target, config, target_mesh_volume_mm3=abs(float(mesh.volume))
    )
    assert len(layers) == 1
    assert metrics.coverage == pytest.approx(1.0, abs=2e-4)
    assert metrics.overfill_ratio == pytest.approx(0.0, abs=2e-4)
    assert metrics.iou == pytest.approx(1.0, abs=2e-4)
    assert metrics.passed


def test_mathematically_bounded_shape_ratios_are_clamped() -> None:
    assert _unit_interval(-1.0e-12) == 0.0
    assert _unit_interval(0.25) == 0.25
    assert _unit_interval(1.0 + 1.0e-12) == 1.0


@pytest.mark.parametrize(
    ("fixture_name", "metric_name"),
    [("shape_underfill", "underfill_ratio"), ("shape_overfill", "overfill_ratio")],
)
def test_shape_failure_modes(fixture_root: Path, fixture_name: str, metric_name: str) -> None:
    job = fixture_root / fixture_name
    config = load_config(job / "config.yaml")
    trajectories = load_trajectory_csv(job / "trajectory.csv", config)
    mesh = load_target_mesh(job / "target.stl", config)
    deposited = build_deposited_layers(trajectories, config)
    target = slice_target_layers(mesh, determine_evaluation_layers(mesh, deposited, config), config)
    metrics, _ = compute_shape_metrics(
        deposited, target, config, target_mesh_volume_mm3=abs(float(mesh.volume))
    )
    assert getattr(metrics, metric_name) > 0.05
    assert not metrics.passed


def test_target_slice_preserves_hole(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    polygon = Polygon(
        [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)],
        holes=[[(-3.0, -3.0), (-3.0, 3.0), (3.0, 3.0), (3.0, -3.0)]],
    )
    mesh = trimesh.creation.extrude_polygon(polygon, 2.0, engine="earcut")
    sliced = slice_target_layers(mesh, [0], config)[0]
    components = polygon_components(sliced)
    assert len(components) == 1
    assert len(components[0].interiors) == 1


def test_deposition_only_layer_is_counted_as_failed(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    target = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    excess = Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])

    metrics, layers = compute_shape_metrics({0: target, 1: excess}, {0: target}, config)

    assert len(layers) == 2
    assert metrics.evaluated_layer_count == 2
    assert metrics.failed_layer_count == 1
    assert metrics.failed_layer_ratio == pytest.approx(0.5)
    assert layers[1].passed is False


def test_shape_progress_reports_empty_mapped_layer(fixture_root: Path) -> None:
    config = load_config(fixture_root / "collision_free" / "config.yaml")
    target = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    progress: list[tuple[float, int, int]] = []

    compute_shape_metrics(
        {1: GeometryCollection()},
        {0: target, 1: GeometryCollection()},
        config,
        progress_callback=lambda fraction, completed, total: progress.append(
            (fraction, completed, total)
        ),
    )

    assert progress[-1] == (1.0, 2, 2)
