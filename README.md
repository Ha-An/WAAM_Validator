# WAAM Validator

WAAM Validator는 **3대 로봇의 WAAM(DED) 작업 계획을 실행 전에 검증**하는
Python 3.11+ 도구입니다. 동일한 World 좌표계에 놓인 `config.yaml`,
`trajectory.csv`, `target.stl`을 읽어 다음 항목을 한 번의 실행으로 확인합니다.

- 작업 시간, 모드별 시간·거리·평균 속도와 로봇 간 workload imbalance
- Robot Base 기준 3D Reach 한계 초과
- 폭이 있는 Base–TCP 2D Capsule의 XY 안전 여유와 TCP 안전 반경 침범
- 적층 경로로부터 재구성한 layer 형상과 Target STL의 Coverage, Underfill,
  Overfill, IoU
- 보고서, JSON/CSV, PNG, 적층 형상 STL과 선택적 3D Replay

CLI, Python API, 단일 작업용 로컬 웹 UI를 제공합니다. 현재 WAAM Validator
애플리케이션 버전은 `1.0`입니다. Config에는 별도 버전 필드를 두지 않으며,
결과 파일 형식의 `schema_version`은 애플리케이션 버전과 독립적입니다.

> 이 Validator는 로봇을 Base–TCP 중심선과 고정 폭의 2D Capsule로 단순화한
> **계획 검증기**입니다.
> 실제 관절·링크, 환경물, 열변형과 용융풀 물리는 계산하지 않습니다.

## 빠른 시작

### 1. 설치

Windows PowerShell 기준:

```powershell
cd C:\Github\WAAM_Validator
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

이미 `.venv`가 준비되어 있다면 활성화와 마지막 두 설치 명령만 실행하면 됩니다.

### 2. 작업 폴더 준비

Validator는 아래 세 파일명을 고정 인터페이스로 사용합니다.

```text
my_job/
├── config.yaml
├── trajectory.csv
└── target.stl
```

Trajectory 헤더는 정확히 다음과 같아야 합니다.

```csv
robot_id,time_s,x_mm,y_mm,z_mm,mode
```

`robot_id`는 `1`, `2`, `3`을 모두 포함하고, 좌표와 STL은 World 좌표계의
밀리미터 단위여야 합니다. `mode`는 현재 행에서 다음 행까지의 동작을 뜻합니다.

| 값 | 의미 | 거리·시간 집계 |
| --- | --- | --- |
| `D` | Deposition | 적층 시간과 적층 거리 |
| `T` | Travel | 비적층 이동 시간과 이동 거리 |
| `W` | Wait | 대기 시간, 허용 오차 내 정지 |

전체 입력 계약은 [WAAM Validator 입력물 인터페이스](docs/WAAM_Validator_입력물_인터페이스.md),
모든 설정 필드는 [Config 참조](docs/config-reference.md)를 확인하십시오.

### 3. 입력 검사와 Validation

```powershell
waam-validator check .\my_job
waam-validator run .\my_job
```

`check`는 파일·Config/CSV 구조·trajectory 의미와 Target 좌표 일관성을 검사하지만 충돌,
layer slicing, 형상 비교와 산출물 생성은 수행하지 않습니다. `run`은 전체
Validation을 수행하며 결과를 기본적으로 아래 새 폴더에 보존합니다.

```text
my_job/output/YYYY-MM-DD_HHMMSS/
```

정상적인 판정 실패는 오류가 아니라 `FAIL` 결과이며 종료 코드 `1`을 반환합니다.
파싱할 수 없거나 계산을 끝낼 수 없는 경우에만 `ERROR`와 종료 코드 `2`~`5`가
사용됩니다.

### 4. 로컬 UI

```powershell
waam-validator ui C:\path\to\my_job
```

브라우저의 `http://127.0.0.1:8050`에서 다음 순서로 진행합니다.

1. **입력 준비** — 폴더 선택, 일회성 입력 확인, 3D 배치·Target·Trajectory·Gantt 확인
2. **Validation 진행** — 단계별 진행률, 처리량, 경과 시간, 예상 잔여 시간 확인
3. **결과** — 판정 요약, 로봇 작업, 충돌 안전, 형상 비교, 파일·Replay 탭 확인

Replay는 기본 생성하지 않습니다. Validation이 끝난 뒤 `파일·Replay` 탭에서 frame
간격과 예상 시간·용량을 확인하고 필요할 때만 생성할 수 있습니다.

입력 화면의 3D 장면에서는 로봇의 고정 설치점인 **Base**와 TCP의 선택적 명목
대기점인 **Home**을 서로 다른 표식으로 보여줍니다. Reach와 Base–TCP Capsule은
항상 Base를 기준으로 계산하며 Home이 Base를 대신하지 않습니다.

## 자주 쓰는 명령

```powershell
# PNG와 HTML 시각화 없이 핵심 결과 생성
waam-validator run .\my_job --headless

# stdout을 단일 JSON 객체로 출력
waam-validator run .\my_job --json

# Validation과 함께 replay.html도 생성
waam-validator run .\my_job --replay

# 정확한 출력 경로 지정(기존 비어 있지 않은 폴더는 거부)
waam-validator run .\my_job --output C:\results\run_001

# 브라우저를 자동으로 열지 않고 다른 포트에서 UI 실행
waam-validator ui .\my_job --port 8051 --no-browser
```

## 결과 요약

대표 산출물은 다음과 같습니다. Config의 `output` 옵션에 따라 일부 파일은 생략될
수 있습니다.

| 파일 | 용도 |
| --- | --- |
| `summary.json` | 자동 처리용 최종 PASS/FAIL과 핵심 지표 |
| `validation_report.md` | 사람이 읽는 종합 보고서 |
| `robot_metrics.csv` | 로봇별 시간·거리·속도·Reach 지표 |
| `collision_events.csv` | Arm Envelope/TCP Radius event의 pair, 시간, 최소 여유와 closest points |
| `layer_metrics.csv` | layer별 면적, Coverage, Underfill, Overfill, IoU |
| `warnings.csv` | warning과 정상 FAIL 원인이 된 비치명 이슈 |
| `deposited.stl` | 경로와 명목 비드 설정으로 재구성한 적층 형상 |
| `run.log` | 입력, 일정, 충돌, 형상 및 판정 실행 로그 |
| `*.png` | XY overview, Gantt, layer 지표, worst-layer와 Arm Envelope 최악 시점 비교 |
| `replay.html` | 3D 장면과 실제 축척 XY Capsule top-view가 있는 선택적 Replay |

각 지표의 분모와 해석, 전체 필드 목록은 [결과 및 산출물 참조](docs/results-reference.md)에
정리되어 있습니다.

## 포함된 검증 자료

- `tests/fixtures/`: 충돌 없음, Arm Capsule 교차·near miss, TCP 안전 반경,
  시간 분리, underfill·overfill을 의도한 작은 회귀 fixture
- `tests/01`–`tests/10`: raster, 원형 벽, 동심 벽, 별형, 격자, 곡선 벽 등 서로
  다른 절차적 모델과 3대 로봇 heuristic trajectory
- `tests/motor`, `tests/twisted_half`: 실제 대형 입력 회귀 자료. 추가 이름의 STL은
  출처 확인용 참조 사본이며 Validator는 각 폴더의 `target.stl`만 읽음
- `scripts/generate_benchmark_jobs.py`: 루트 `config.yaml`을 기준으로 10개 입력을
  결정론적으로 다시 생성하는 스크립트
- `tests/benchmark_results.md`: WAAM Validator 1.0으로 다시 측정한 결과와 해석 범위

폴더별 용도와 원본 참조 STL의 차이는 [검증 데이터 안내](tests/README.md)를 확인하십시오.

각 작업의 `output/`은 실행할 때 다시 생성되는 산출물이므로 Git에서 제외합니다.
저장소에는 재현에 필요한 입력 세 파일과 결과 요약 문서만 보관합니다.

## 현재 검증 상태

2026-09-06 기준 현재 작업 트리에서 10개 절차적 benchmark와 Motor가 PASS했고,
Twisted Half는 예상된 Robot Reach 초과만으로 FAIL했습니다. Twisted의 Arm Envelope와
TCP Radius event는 0건이고 형상 비교는 PASS입니다. 10만 행 성능 시험의 peak RSS는
201.36 MiB로 512 MiB 인수 기준을 충족했습니다.

자동 테스트, 실제 모델 수치, 과거 traceback과 미완성 output 처리, Git 추적 파일 점검은
[릴리스 준비 및 검증 기록](docs/release-readiness.md)에 정리했습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [문서 안내](docs/README.md) | 목적별 문서 찾기 |
| [설치 및 시작하기](docs/getting-started.md) | 환경 구성, 첫 실행, CLI 사용 패턴 |
| [입력물 인터페이스](docs/WAAM_Validator_입력물_인터페이스.md) | 알고리즘이 생성해야 할 세 입력 파일의 상세 계약 |
| [Config 참조](docs/config-reference.md) | 모든 설정 필드의 의미, 단위와 제약 |
| [검증 방법과 판정 기준](docs/validation-method.md) | 시간, Reach, 충돌, 적층·형상 알고리즘과 한계 |
| [UI 사용 안내](docs/ui-guide.md) | 입력 준비부터 결과·Replay 확인까지의 화면 흐름 |
| [결과 및 산출물 참조](docs/results-reference.md) | JSON/CSV/보고서, 종료 코드와 결과 해석 |
| [Python API](docs/python-api.md) | 공개 함수, 주요 데이터 타입과 진행 콜백 |
| [개발자 안내](docs/development.md) | 코드 구조, 테스트, 정적 검사와 기여 시 주의사항 |
| [버전 및 호환성](docs/versioning.md) | 애플리케이션 버전, Config와 결과 schema의 관리 기준 |
| [릴리스 준비 및 검증 기록](docs/release-readiness.md) | 실제 모델·성능·로그·Git 추적 상태 감사 결과 |
| [검증 데이터 안내](tests/README.md) | fixture·benchmark·실제 모델 폴더와 output 정책 |
| [10개 모델 benchmark 결과](tests/benchmark_results.md) | 절차적 모델·trajectory 생성 방식과 실제 Validation 결과 |

## 설계 원칙

- 원본 입력 파일을 복사하거나 수정하지 않습니다.
- 같은 입력의 핵심 수치와 event·CSV 행 순서는 결정론적으로 생성합니다. 출력 경로와
  `run.log` 시각처럼 실행마다 달라지는 메타데이터는 제외합니다.
- 시간 통계는 adaptive sample이 아닌 원본 interval에서 계산합니다.
- 시각화 preview의 downsampling은 판정 결과에 영향을 주지 않습니다.
- 실행 결과는 매번 새 폴더에 저장해 이전 결과를 덮어쓰지 않습니다.
- UI는 기본적으로 `127.0.0.1`에만 열리며 인증·외부 배포 기능을 제공하지 않습니다.

## 범위 밖의 항목

현재 구현은 실제 로봇 관절 자세, 링크 간 3D 충돌, 지그·설비·환경 충돌, 경로
생성·수정, 열전달, 용융풀, 잔류응력과 공정 안정성을 다루지 않습니다. 따라서
`PASS`는 이 저장소가 구현한 기하·일정 기준을 만족한다는 뜻이며, 실제 장비 운전의
안전이나 제작 품질을 보증하지 않습니다.

## Capsule 연동 주의

각 Robot에는 `arm_envelope_radius_mm`, collision에는 `check_arm_envelope`와
`arm_clearance_mm`이 필수입니다. 구형 `check_arm_crossing` Config는 자동 변환하지
않습니다. `Multi_robot_DED_NCO`가 기존 Validator Config를 계속 생성한다면 이 세
필드를 추가하도록 후속 연동이 필요하며, 이번 변경은 해당 워크스페이스 자체를
수정하지 않았습니다.
