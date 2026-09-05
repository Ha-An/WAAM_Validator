from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
import yaml


@pytest.mark.performance
def test_100k_rows_under_512_mb(fixture_root: Path, tmp_path: Path) -> None:
    job = tmp_path / "large-job"
    job.mkdir()
    shutil.copyfile(fixture_root / "collision_free" / "target.stl", job / "target.stl")
    config = yaml.safe_load((fixture_root / "collision_free" / "config.yaml").read_text())
    config["simulation"]["max_time_step_s"] = 10.0
    config["simulation"]["max_tcp_step_mm"] = 1000.0
    (job / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    counts = (33334, 33333, 33333)
    bases = ((-1000.0, -600.0), (1000.0, -600.0), (0.0, 1200.0))
    with (job / "trajectory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("robot_id", "time_s", "x_mm", "y_mm", "z_mm", "mode"))
        for robot_id, (count, base) in enumerate(zip(counts, bases, strict=True), start=1):
            for index in range(count):
                writer.writerow((robot_id, index * 10.0, base[0], base[1], 100.0, "W"))
    started = time.perf_counter()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "waam_validator.cli",
            "run",
            str(job),
            "--output",
            str(tmp_path / "large-output"),
            "--headless",
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    monitored = psutil.Process(process.pid)
    peak_rss = 0
    while process.poll() is None:
        try:
            rss = monitored.memory_info().rss + sum(
                child.memory_info().rss for child in monitored.children(recursive=True)
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            rss = 0
        peak_rss = max(peak_rss, rss)
        time.sleep(0.02)
    stdout, stderr = process.communicate()
    elapsed_s = time.perf_counter() - started
    print(
        f"PERFORMANCE rows=100000 peak_rss_mib={peak_rss / (1024**2):.2f} elapsed_s={elapsed_s:.2f}"
    )
    assert process.returncode == 1, stderr
    assert '"status":"FAIL"' in stdout
    assert peak_rss < 512 * 1024 * 1024
