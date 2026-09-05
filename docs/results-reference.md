# 결과 및 산출물 참조

## 출력 폴더 정책

기본 실행은 입력 작업 폴더 아래에 매번 새 run 디렉터리를 만듭니다.

```text
my_job/
└── output/
    ├── 2026-09-05_101530/
    └── 2026-09-05_101530_001/
```

같은 초에 실행해 경로가 겹치면 `_001`, `_002` suffix를 붙입니다. 이전 결과를
삭제하거나 덮어쓰지 않습니다. `--output PATH`를 사용하면 정확한 경로를 쓰며 기존
비어 있지 않은 폴더는 거부합니다.

Config의 `output` flag에 따라 선택 산출물이 달라질 수 있습니다. `run.log`와
`warnings.csv`는 정상 계산 과정에서 항상 생성됩니다. 치명 오류가 생기면 가능한
경우 `error.json`을 남깁니다.

## 결과 상태

| 상태 | 의미 | CLI 종료 코드 |
| --- | --- | --- |
| `PASS` | 활성화된 판정 기준을 모두 만족 | `0` |
| `FAIL` | 계산은 완료했으나 한 개 이상의 기준을 위반 | `1` |
| `ERROR` | 입력·Target·계산·출력 문제로 판정을 완료하지 못함 | `2`~`5` |

`FAIL`은 예외가 아니므로 전체 결과 파일을 저장하고 traceback 없이 종료합니다.

## `summary.json`

자동화가 가장 먼저 읽어야 하는 고정 schema의 핵심 결과입니다.

```json
{
  "schema_version": "1.1",
  "status": "PASS",
  "input": {},
  "schedule": {},
  "reach": {},
  "collision": {},
  "shape": {},
  "failure_reasons": [],
  "warnings": [],
  "errors": [],
  "output_directory": "..."
}
```

### `input`

| 필드 | 의미 |
| --- | --- |
| `directory` | canonical 입력 폴더 절대 경로 |
| `trajectory_rows` | 세 로봇을 합친 CSV 행 수 |
| `target_watertight` | 로딩·선택적 repair 후 Target watertight 상태 |

### `schedule`

| 필드 | 의미 |
| --- | --- |
| `makespan_s` | 세 completion 중 최댓값 |
| `robot_completion_s` | robot ID 문자열별 마지막 timestamp |
| `workload_imbalance_s` | 최대 completion과 최소 completion의 차이 |
| `normalized_imbalance` | imbalance / makespan |

### `reach`

`passed`와 로봇별 `robots` 배열을 포함합니다.

| 로봇 필드 | 의미 |
| --- | --- |
| `robot_id` | 1, 2, 3 |
| `passed` | Reach 위반 절점이 없는지 |
| `reach_radius_mm` | Config의 3D Reach 반경 |
| `maximum_reach_mm` | Base에서 가장 먼 원본 TCP 절점까지의 3D 거리 |
| `minimum_margin_mm` | 설정 반경 - 최대 사용 거리; 음수이면 초과 |
| `utilization_ratio` | 최대 사용 거리 / 설정 반경 |
| `violation_point_count` | Reach 밖의 원본 절점 수 |
| `first_violation_s`, `last_violation_s` | 최초·최종 위반 시각, 없으면 `null` |

### `collision`

| 필드 | 의미 |
| --- | --- |
| `passed` | 활성 검사에서 event가 하나도 없는지 |
| `collision_event_count` | 두 종류 event 총수 |
| `arm_cross_event_count` | ARM_CROSS event 수 |
| `tcp_radius_event_count` | TCP_RADIUS event 수 |
| `minimum_tcp_distance_mm` | 전체 sample과 pair의 최소 TCP XY 거리 |
| `minimum_tcp_pair` | 그 최소값을 가진 robot pair |
| `minimum_required_distance_mm` | 해당 pair TCP 반경 합 |
| `checks_enabled` | arm crossing, TCP radius 활성 여부 |

### `shape`

| 필드 | 의미 |
| --- | --- |
| `passed` | 모든 전체 형상 threshold 통과 여부 |
| `target_volume_mm3` | layer 단면 적분 Target 체적 |
| `deposited_volume_mm3` | layer union 적분 적층 체적 |
| `coverage` | 교집합 체적 / Target 체적 |
| `underfill_ratio` | 미적층 Target 체적 / Target 체적 |
| `overfill_ratio` | Target 밖 적층 체적 / Target 체적 |
| `iou` | 교집합 체적 / 합집합 체적 |
| `failed_layer_count` | 개별 IoU 기준 실패 layer 수 |
| `evaluated_layer_count` | Target 또는 deposition이 존재하는 평가 layer 수 |
| `failed_layer_ratio` | 실패 layer 수 / 평가 layer 수 |
| `target_mesh_volume_mm3` | STL mesh 자체의 절대 체적 |
| `target_volume_discrepancy_ratio` | mesh 체적과 layer 적분 체적의 상대 차이 |

`failure_reasons`는 최종 FAIL을 만든 문장을 판정 순서대로 담습니다. `warnings`는
판정을 반드시 실패시키지는 않는 이슈, `errors`는 실행 가능한 process 위반을
담습니다.

## `robot_metrics.csv`

한 행이 한 로봇입니다.

| 필드 | 의미 |
| --- | --- |
| `robot_id` | 로봇 ID |
| `completion_s` | 마지막 timestamp |
| `deposition_time_s`, `travel_time_s`, `wait_time_s` | 원본 interval mode별 누적 시간 |
| `deposition_ratio`, `travel_ratio`, `wait_ratio` | 각 누적 시간 / 해당 로봇 completion |
| `deposition_length_mm`, `travel_length_mm` | D/T interval의 3D 누적 길이 |
| `mean_deposition_speed_mm_s`, `mean_travel_speed_mm_s` | 거리 / 해당 mode 시간; 시간이 0이면 빈 값 |
| `reach_radius_mm` | 설정 Reach 반경 |
| `maximum_reach_mm` | 최대 Base–TCP 3D 거리 |
| `reach_margin_mm` | 설정 반경 - 최대 거리 |
| `reach_utilization_ratio` | 최대 거리 / 설정 반경 |
| `reach_violation_point_count` | 범위 밖 원본 절점 수 |

중요하게, CSV의 mode ratio 분모는 **로봇별 completion**입니다. UI의 공통 작업시간
비율 그래프는 비교를 위해 **전체 makespan**을 분모로 사용할 수 있으므로 두 수치의
의도를 구분해야 합니다.

## `collision_events.csv`

한 행이 병합된 한 충돌 event입니다. Event가 없으면 header만 있는 빈 CSV입니다.

| 필드 | 의미 |
| --- | --- |
| `event_id` | 결정론적으로 정렬한 1-based ID |
| `type` | `ARM_CROSS` 또는 `TCP_RADIUS` |
| `robot_a`, `robot_b` | robot pair |
| `start_s`, `end_s`, `duration_s` | 첫 true, 마지막 true와 그 차이 |
| `min_tcp_distance_mm` | TCP_RADIUS event 내부 최소 거리; 다른 종류는 빈 값 |
| `required_tcp_distance_mm` | 해당 pair의 요구 거리; 다른 종류는 빈 값 |
| `crossing_x_mm`, `crossing_y_mm` | ARM_CROSS 대표 XY 위치; 다른 종류는 빈 값 |

짧은 false gap이 설정 한계 이하면 event 범위에 포함되지만 end는 마지막 true sample
시각입니다. 표에 없는 내부 marker와 최소거리 시각은 Replay 표시 계산에 사용될 수
있습니다.

## `layer_metrics.csv`

한 행이 Target 또는 deposition이 존재하는 한 layer입니다.

| 필드 | 의미 |
| --- | --- |
| `layer_index` | 0-based layer index |
| `z_bottom_mm`, `z_top_mm`, `z_slice_mm` | layer 경계와 Target slice 높이 |
| `target_area_mm2` | Target 단면 면적 |
| `deposited_area_mm2` | union된 명목 적층 면적 |
| `intersection_area_mm2` | Target과 적층의 교집합 면적 |
| `underfill_area_mm2` | Target 안에서 비어 있는 면적 |
| `overfill_area_mm2` | Target 밖으로 적층된 면적 |
| `coverage` | intersection / target |
| `underfill_ratio` | underfill / target |
| `overfill_ratio` | overfill / target; Target이 비면 빈 값 |
| `iou` | intersection / union |
| `passed` | `minimum_layer_iou` 통과 여부 |

전체 지표는 이 행들의 비율을 단순 평균한 값이 아니라 면적에 layer 높이를 곱한 체적
합계로 다시 계산합니다.

## `warnings.csv`

| 필드 | 의미 |
| --- | --- |
| `severity` | `warning` 또는 `error` |
| `code` | 안정적인 machine-readable 이슈 코드 |
| `message` | 사람이 읽는 설명 |
| `robot_id` | 해당 시 로봇 ID |
| `start_s`, `end_s` | 해당 시 interval 또는 위반 시간 범위 |

여기서 `error` severity는 반드시 프로세스가 중단됐다는 뜻이 아닙니다. W 이동,
Reach, 설정에 따라 치명적인 속도 위반처럼 **전체 계산이 가능한 정상 FAIL 이슈**도
이 파일에 기록됩니다. 실행 중단 오류는 `error.json`과 종료 코드로 구분합니다.

## 사람이 읽는 파일

### `validation_report.md`

입력, Schedule, Robot Reach, Collision, Shape, Failure Reasons, warning/error, 산출물과
해석 한계를 한 문서에 요약합니다.

### `run.log`

입력 로딩, trajectory 행 수, Target 면 수, makespan, 각 로봇 D/T/W 시간·거리·속도,
Reach 사용량, 충돌 sample/event 수, layer 범위, 형상 지표와 최종 판정 근거를 시간
순서로 기록합니다. 결과값이 예상과 다르면 가장 먼저 확인할 진단 파일입니다.

## 형상과 시각화 파일

| 파일 | 생성 조건 | 의미 |
| --- | --- | --- |
| `deposited.stl` | `save_deposited_stl: true` | layer union polygon을 extrusion한 명목 적층 형상 |
| `overview_xy.png` | 일반 모드 + `save_static_plots` | Base, workspace, trajectory, 충돌의 XY 개요 |
| `gantt.png` | 일반 모드 + `save_static_plots` | 로봇별 mode timeline |
| `shape_metrics_by_layer.png` | 일반 모드 + `save_static_plots` | layer별 Coverage/IoU 등 |
| `worst_layer_comparison.png` | 일반 모드 + `save_static_plots` | 가장 나쁜 layer의 Target/Deposition 비교 |
| `replay.html` | `--replay` 또는 UI 별도 생성 | 외부 서버가 필요 없는 Plotly 3D Replay |

`--headless`는 PNG와 HTML만 생략하며 핵심 판정 파일을 바꾸지 않습니다.

## UI 실행 보조 파일

UI에서 시작한 run에는 다음 내부 상태 파일이 있을 수 있습니다.

| 파일 | 용도 |
| --- | --- |
| `dashboard_status.json` | Validation worker 진행·종료 상태 전달 |
| `validation_inputs.json` | 실행 당시 세 입력 signature 기록 |
| `replay_status.json` | Replay worker 진행 상태 |
| `replay_manifest.json` | 생성 interval, frame 수, 시간·용량 정보 |

이 파일들은 결과 판정을 재계산하는 입력이 아닙니다. 자동 분석에서는
`summary.json`과 세 metrics CSV를 우선 사용하십시오.

## `error.json`

치명 오류 시 best-effort로 다음 구조를 기록합니다.

```json
{
  "schema_version": "1.1",
  "status": "ERROR",
  "code": "...",
  "message": "...",
  "input_directory": "..."
}
```

디스크·권한 문제로 output 자체를 쓸 수 없다면 `error.json`도 없을 수 있으므로 CLI
종료 코드와 stderr를 함께 보관해야 합니다.

## 종료 코드

| 코드 | 분류 | 예시 |
| --- | --- | --- |
| `0` | PASS | 활성 기준 모두 통과 |
| `1` | 정상 FAIL | Reach, 충돌, 형상 또는 process 기준 위반 |
| `2` | 입력 오류 | 누락 파일, Config/CSV schema, D layer/workspace 오류 |
| `3` | Target 오류 | STL 로딩·유효성·좌표 문제 |
| `4` | 계산 오류 | polygon/numerical/internal calculation 실패 |
| `5` | 출력 오류 | 비어 있지 않은 explicit output, 쓰기 실패 |

PowerShell에서는 실행 직후 `$LASTEXITCODE`로 확인할 수 있습니다.

```powershell
waam-validator run C:\data\my_job --headless --json
$LASTEXITCODE
```

자동화에서는 코드 `0`과 `1`을 모두 **계산 완료**로 취급하고 `summary.json`의
`status`를 읽는 것이 안전합니다. 코드 `2`~`5`는 미완료로 처리하십시오.

[문서 안내로 돌아가기](README.md)
