# Config 참조

`config.yaml`은 작업 좌표계, 로봇 배치, 공정 명목값, 충돌·형상 판정 기준과 산출물
정책을 정의합니다. 현재는 `schema_version: '1.1'`만 허용하며, 알려지지 않은 필드는
오타로 간주해 거부합니다. 숫자에는 `NaN`과 무한대를 사용할 수 없습니다.

완전한 예제는 [sample config](../examples/sample_job/config.yaml)를 참고하십시오.

## 최상위 구조

```yaml
schema_version: '1.1'
simulation: { ... }
robots: [ ... ]
process: { ... }
workspace: { ... }
collision: { ... }
validation: { ... }
shape_validation: { ... }
output: { ... }
```

모든 거리와 좌표는 millimetre, 시간은 second, 속도는 millimetre/second입니다.

## `simulation`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `max_time_step_s` | float, `> 0` | 충돌용 adaptive timeline의 최대 시간 간격 |
| `max_tcp_step_mm` | float, `> 0` | 충돌 timeline에서 한 sample 사이 TCP 최대 이동량 |
| `event_merge_gap_s` | float, `>= 0` | 동일 종류·pair event 사이 false gap 병합 한계 |
| `batch_size` | int, `>= 1000` | streaming/batch 계산 단위 |

충돌 계산은 원본 timestamp 합집합을 보존하면서 시간 간격과 세 로봇의 TCP 이동량
조건을 모두 만족하도록 구간을 세분합니다. 값을 작게 하면 검출 해상도와 계산량이
함께 증가합니다.

## `robots`

정확히 세 항목이어야 하며 ID `1`, `2`, `3`이 각각 한 번씩 있어야 합니다.

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `id` | `1`, `2`, `3` | trajectory의 `robot_id`와 연결되는 식별자 |
| `base_xyz_mm` | float 3개 | World 좌표계의 Robot Base 위치 |
| `tcp_radius_mm` | float, `> 0` | TCP 충돌 검사에 사용하는 안전 반경 |
| `reach_radius_mm` | float, `> 0` | Base 중심 3D 구형 Reach 한계 |

세 Base 좌표는 서로 달라야 하며 XY 투영이 면적을 갖는 삼각형을 이루어야 합니다.
Reach는 `sqrt((x-bx)^2 + (y-by)^2 + (z-bz)^2)`로 계산합니다.

## `process`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `deposition_speed_mm_s` | float, `> 0` | Deposition 명목 속도 |
| `travel_speed_mm_s` | float, `> 0` | Travel 명목 속도 |
| `layer_height_mm` | float, `> 0` | layer 높이와 적층 STL extrusion 높이 |
| `bead_width_mm` | float, `> 0` | D centerline을 buffer할 명목 비드 폭 |
| `build_plane_z_mm` | float | 첫 layer가 시작되는 build plane Z |
| `tcp_z_reference` | `top` 또는 `center` | D 경로 Z를 layer의 상면 또는 중심으로 해석 |

명목 속도와 실제 interval 평균 속도의 허용 범위는 `validation` 설정에서 정합니다.

## `workspace`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `shape` | `circle_xy` | 현재 지원하는 workspace 형상 |
| `center_xy_mm` | float 2개 | World XY의 원 중심 |
| `radius_mm` | float, `> 0` | 원형 적층 영역 반경 |

Workspace 원 전체가 세 Robot Base의 XY 삼각형 내부에 있어야 합니다. D centerline만이
아니라 명목 bead 외곽까지 workspace 안에 있어야 하므로, 실제 허용 centerline
반경은 대략 `radius_mm - bead_width_mm / 2`입니다.

## `collision`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `check_arm_crossing` | bool | Base–TCP XY 선분 교차 검사 활성화 |
| `check_tcp_radius` | bool | 두 TCP의 XY 안전 반경 침범 검사 활성화 |
| `touching_is_collision` | bool | 접점과 임계거리 동일 상태를 충돌로 볼지 결정 |
| `geometry_epsilon_mm` | float, `> 0` | 선분·접점 계산의 기하 허용 오차 |

검사를 끄면 해당 항목은 FAIL 원인이 되지 않으며 warning이 기록됩니다. TCP 반경
검사를 끄더라도 전체 최소 TCP 거리는 지표로 계산합니다.

## `validation`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `wait_position_tolerance_mm` | float, `>= 0` | W 구간에서 허용할 위치 변화 |
| `layer_z_tolerance_mm` | float, `>= 0` | D 구간의 layer Z 일치 허용 오차 |
| `speed_relative_tolerance` | float, `>= 0` | D/T 명목 속도에 대한 상대 허용 오차 |
| `fail_on_speed_violation` | bool | 속도 위반을 warning이 아닌 정상 FAIL로 처리 |
| `require_watertight_target` | bool | Target watertight를 필수로 요구 |
| `attempt_target_repair` | bool | 제한적인 Target mesh repair 시도 |
| `target_volume_discrepancy_warning_ratio` | float, `>= 0` | mesh 체적과 layer 적분 체적 차이 warning 기준 |

W 위치 위반은 계산 가능한 정상 FAIL입니다. Stationary T와
`fail_on_speed_violation: false`일 때의 속도 위반은 warning입니다. Layer를 정의할
수 없는 D 구간과 workspace 위반은 입력 오류로 실행을 중단합니다.

## `shape_validation`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `polygon_buffer_resolution` | int, `>= 1` | round cap/곡선 buffer 근사 해상도 |
| `polygon_snap_tolerance_mm` | float, `> 0` | layer polygon precision snapping 단위 |
| `minimum_overall_coverage` | float, `0..1` | 전체 최소 Coverage |
| `maximum_overall_overfill_ratio` | float, `>= 0` | 전체 최대 Overfill ratio |
| `minimum_overall_iou` | float, `0..1` | 전체 최소 IoU |
| `minimum_layer_iou` | float, `0..1` | 개별 layer PASS의 최소 IoU |
| `maximum_failed_layer_ratio` | float, `0..1` | 전체 허용 실패 layer 비율 |
| `area_epsilon_mm2` | float, `> 0` | 미소 polygon 제거와 빈 면적 판정 오차 |

전체 형상 PASS에는 Coverage, Overfill, IoU, failed-layer ratio 조건을 모두 만족해야
합니다. 개별 layer는 `minimum_layer_iou`만으로 PASS/FAIL을 정하고, 그 집계가
`maximum_failed_layer_ratio`에 사용됩니다.

## `output`

| 필드 | 타입·제약 | 의미 |
| --- | --- | --- |
| `save_summary_json` | bool | `summary.json` 저장 |
| `save_report_markdown` | bool | `validation_report.md` 저장 |
| `save_collision_events_csv` | bool | `collision_events.csv` 저장 |
| `save_robot_metrics_csv` | bool | `robot_metrics.csv` 저장 |
| `save_layer_metrics_csv` | bool | `layer_metrics.csv` 저장 |
| `save_deposited_stl` | bool | `deposited.stl` 저장 |
| `save_static_plots` | bool | 일반 모드에서 PNG 저장 |
| `save_interactive_html` | bool | 호환용 설정; Replay 생성은 실행 옵션으로 명시 |
| `animation_sample_interval_s` | float, `> 0` | CLI Replay의 기본 frame 간격 |

`--headless`는 PNG와 HTML 시각화를 생략합니다. `replay.html`은 기본 Validation에서
만들지 않으며 CLI의 `--replay` 또는 UI의 별도 생성 동작이 필요합니다.

UI에서 결과 KPI와 각 탭을 모두 사용하려면 `save_summary_json`, 세 metrics CSV,
`save_report_markdown`을 `true`로 두는 것을 권장합니다. 판정 자체는 수행되더라도 이
파일을 끄면 해당 결과 화면 또는 다운로드 항목이 비어 있을 수 있습니다.

## 최소 편집 예제

```yaml
schema_version: '1.1'
simulation:
  max_time_step_s: 0.1
  max_tcp_step_mm: 5.0
  event_merge_gap_s: 0.2
  batch_size: 10000
robots:
  - id: 1
    base_xyz_mm: [-1000.0, -600.0, 0.0]
    tcp_radius_mm: 20.0
    reach_radius_mm: 2500.0
  - id: 2
    base_xyz_mm: [1000.0, -600.0, 0.0]
    tcp_radius_mm: 20.0
    reach_radius_mm: 2500.0
  - id: 3
    base_xyz_mm: [0.0, 1200.0, 0.0]
    tcp_radius_mm: 20.0
    reach_radius_mm: 2500.0
process:
  deposition_speed_mm_s: 8.0
  travel_speed_mm_s: 150.0
  layer_height_mm: 2.0
  bead_width_mm: 4.0
  build_plane_z_mm: 0.0
  tcp_z_reference: top
workspace:
  shape: circle_xy
  center_xy_mm: [0.0, 0.0]
  radius_mm: 250.0
collision:
  check_arm_crossing: true
  check_tcp_radius: true
  touching_is_collision: true
  geometry_epsilon_mm: 1.0e-6
validation:
  wait_position_tolerance_mm: 0.001
  layer_z_tolerance_mm: 0.25
  speed_relative_tolerance: 0.1
  fail_on_speed_violation: false
  require_watertight_target: true
  attempt_target_repair: false
  target_volume_discrepancy_warning_ratio: 0.02
shape_validation:
  polygon_buffer_resolution: 8
  polygon_snap_tolerance_mm: 0.0001
  minimum_overall_coverage: 0.95
  maximum_overall_overfill_ratio: 0.05
  minimum_overall_iou: 0.90
  minimum_layer_iou: 0.80
  maximum_failed_layer_ratio: 0.05
  area_epsilon_mm2: 1.0e-6
output:
  save_summary_json: true
  save_report_markdown: true
  save_collision_events_csv: true
  save_robot_metrics_csv: true
  save_layer_metrics_csv: true
  save_deposited_stl: true
  save_static_plots: true
  save_interactive_html: false
  animation_sample_interval_s: 1.0
```

[문서 안내로 돌아가기](README.md)
