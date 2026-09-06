# 10개 절차적 모델 Validation 결과

실행일: 2026-09-06

기준 Config: 저장소 루트의 `config.yaml`

생성기: [`scripts/generate_benchmark_jobs.py`](../scripts/generate_benchmark_jobs.py)

## 목적과 범위

서로 다른 경로 형태와 topology를 가진 10개 모델을 절차적으로 생성하고, 간단한
contour/path scheduling 휴리스틱으로 `trajectory.csv`를 만든 뒤 실제
`waam-validator run` 전체 파이프라인을 실행했다. 외부 STL은 사용하지 않아 출처와
라이선스에 관계없이 같은 입력을 다시 생성할 수 있다.

각 [`01`](01/)–[`10`](10/) 폴더에는 Validator가 요구하는 다음 세 입력이 있다.

- `config.yaml`: 루트 Config의 동일한 복사본
- `trajectory.csv`: 휴리스틱이 생성한 3대 로봇 경로
- `target.stl`: 중심선을 bead 폭으로 buffer한 footprint를 적층 높이만큼 extrusion한 목표 형상

일반 실행에서 각 작업의 `output/<timestamp>/`에는 `summary.json`, 보고서, metrics
CSV, PNG, `deposited.stl`, `run.log`를 포함한 핵심 산출물이 생성됐다. 아래 실행 시간은
시각화 생성 시간을 제외해 계산 성능을 비교하기 쉽도록 `--headless`로 다시 측정했다.
Replay는 기본 정책대로 생성하지 않았다. `output/`은 재생성 가능한 실행 결과이므로
Git에는 포함하지 않는다.

## 모델 구성

| 번호 | 모델 | 형상 특징 | Layer | Trajectory 행 |
| --- | --- | --- | ---: | ---: |
| 01 | `raster_plate` | 평행 raster로 채운 판형 모델 | 6 | 352 |
| 02 | `circular_ring` | 원형 단일 thin-wall | 8 | 2,132 |
| 03 | `concentric_rings` | 서로 분리된 동심 이중 벽 | 7 | 3,063 |
| 04 | `star_frame` | 오목 꼭짓점을 가진 별형 폐곡선 | 6 | 268 |
| 05 | `reinforced_cross` | 직교·대각선이 만나는 보강 십자 | 5 | 179 |
| 06 | `honeycomb_cluster` | 다중 cell 육각 격자 | 6 | 658 |
| 07 | `sinusoidal_panel` | 평행한 곡선형 벽 배열 | 7 | 8,033 |
| 08 | `spiral_wall` | 연속 곡률의 나선형 벽 | 8 | 3,268 |
| 09 | `triangular_truss` | 외곽·중앙 보강 삼각 truss | 6 | 256 |
| 10 | `waam_wordmark` | 다중 stroke WAAM 문자 모델 | 5 | 194 |

## 경로 생성 휴리스틱

1. 각 모델을 여러 2D centerline path로 정의한다.
2. 각 path를 로봇 Base까지의 거리와 이미 할당된 path 길이를 함께 고려해 3대 로봇에
   greedy 방식으로 분배한다.
3. 로봇별로 현재 TCP에서 가까운 path 끝점을 다음 시작점으로 고르고 필요하면 path
   방향을 뒤집는다.
4. Config의 `travel_speed_mm_s=150`, `deposition_speed_mm_s=8`,
   `arc_on_time_s=1`, `arc_off_time_s=1`, `safe_travel_z_mm=1700`을 시간 계산에
   그대로 사용한다.
5. D 좌표는 각 layer의 중앙 Z에 놓고, 안전 높이를 거쳐 이동하고, path 사이와 작업
   전후에는 W 구간을 삽입한다.
6. 충돌 회피용 단순 정책으로 한 layer 안에서는 한 번에 한 로봇만 작업하게 한다.
   이 benchmark의 목적은 충돌을 유도하는 것이 아니라 다양한 형상의 정상 경로를
   end-to-end로 검증하는 것이다.

## 실행 결과

| 번호 | 판정 | Makespan [s] | 실제 계산 [s] | 최소 TCP [mm] | 최대 Reach [%] | Coverage [%] | IoU [%] | 충돌 | 실패 Layer | 결과 run |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 01 | PASS | 4,139.066 | 0.693 | 820.000 | 87.321 | 100.0000 | 100.0000 | 0 | 0 | `2026-09-06_143050` |
| 02 | PASS | 1,874.880 | 0.697 | 890.330 | 87.321 | 99.9993 | 99.9992 | 0 | 0 | `2026-09-06_143051` |
| 03 | PASS | 2,399.240 | 1.350 | 846.833 | 87.321 | 99.9988 | 99.9986 | 0 | 0 | `2026-09-06_143051` |
| 04 | PASS | 1,497.859 | 0.997 | 881.040 | 87.321 | 99.9999 | 100.0000 | 0 | 0 | `2026-09-06_143053` |
| 05 | PASS | 1,340.264 | 0.813 | 889.858 | 87.321 | 100.0000 | 100.0000 | 0 | 0 | `2026-09-06_143054` |
| 06 | PASS | 1,795.068 | 1.147 | 928.205 | 87.321 | 100.0000 | 99.9999 | 0 | 0 | `2026-09-06_143055` |
| 07 | PASS | 3,101.609 | 6.083 | 810.024 | 87.321 | 99.9988 | 99.9986 | 0 | 0 | `2026-09-06_143056` |
| 08 | PASS | 2,865.785 | 2.761 | 830.332 | 87.321 | 99.9982 | 99.9982 | 0 | 0 | `2026-09-06_143102` |
| 09 | PASS | 2,207.881 | 1.183 | 827.517 | 87.321 | 100.0000 | 100.0000 | 0 | 0 | `2026-09-06_143105` |
| 10 | PASS | 1,492.319 | 0.915 | 877.133 | 87.321 | 99.9997 | 99.9981 | 0 | 0 | `2026-09-06_143106` |

모든 작업은 사전 입력 검사와 전체 Validation에서 PASS했고, warning은 없었다. 최대
Reach 사용률이 모두 같은 이유는 각 trajectory가 같은 Config의 Home 좌표에서 시작하고
끝나며, 그 Home 좌표가 해당 작업에서 Base로부터 가장 먼 절점이기 때문이다.

WAAM Validator 1.0은 Coverage, Underfill와 IoU처럼 이론상 단위 구간에 속하는 값을
`[0, 1]`로 제한한다. 따라서 polygon 면적 연산에서 생길 수 있는 미세한 부동소수점
초과값도 JSON·CSV에서 수학적 범위를 유지한다. Overfill ratio는 실제로 1을 넘을 수
있으므로 상한을 제한하지 않는다.

기계 판독용 원시 수치는 [`benchmark_results.csv`](benchmark_results.csv)에 보존했다.

## 해석상 주의사항

이번 10개 target과 trajectory는 동일한 parametric centerline에서 생성했다. 따라서
Coverage와 IoU가 거의 100%인 것은 의도된 positive consistency test 결과다. 이
자료는 입력 파싱, 시간 계산, reach, 충돌 scan, 적층 형상 구성, STL slicing, 보고서
생성을 함께 회귀 검사하기에는 적합하지만, 독립적인 slicer/경로 계획 알고리즘의 형상
정확도를 입증하는 자료는 아니다.

후속 강건성 시험에는 다음을 별도 세트로 추가하는 것이 좋다.

- centerline을 일부 누락하거나 바깥으로 이동시킨 underfill/overfill 입력
- 동시에 움직이는 로봇으로 arm crossing 및 TCP radius event를 유도하는 입력
- 외부 CAD STL에서 독립적으로 slicing한 trajectory
- Reach 경계와 workspace 경계를 의도적으로 넘는 입력

## 재생성 및 재실행

저장소 루트에서 다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe scripts\generate_benchmark_jobs.py

1..10 | ForEach-Object {
    $job = "tests\{0:D2}" -f $_
    .\.venv\Scripts\waam-validator.exe check $job
    .\.venv\Scripts\waam-validator.exe run $job --headless --json
}
```

생성기는 입력 세 파일만 결정론적으로 다시 쓰며 기존 `output` 폴더는 삭제하지 않는다.
