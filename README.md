# WAAM Validator

WAAM Validator는 **3대 로봇의 WAAM(DED) 작업 계획을 실행 전에 검증**하는
Python 3.11+ 도구입니다. 동일한 World 좌표계에 놓인 `config.yaml`,
`trajectory.csv`, `target.stl`을 읽어 다음 항목을 한 번의 실행으로 확인합니다.

- 작업 시간, 모드별 시간·거리·평균 속도와 로봇 간 workload imbalance
- Robot Base 기준 3D Reach 한계 초과
- Base–TCP 선분의 XY 교차와 TCP 안전 반경 침범
- 적층 경로로부터 재구성한 layer 형상과 Target STL의 Coverage, Underfill,
  Overfill, IoU
- 보고서, JSON/CSV, PNG, 적층 형상 STL과 선택적 3D Replay

CLI, Python API, 단일 작업용 로컬 웹 UI를 제공합니다. 현재 패키지 버전은
`2.0.0`, 입력·결과 schema는 `1.1`입니다.

> 이 Validator는 로봇을 Base–TCP 직선으로 단순화한 **계획 검증기**입니다.
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

전체 입력 계약은 [WAAM Validator 입력물 인터페이스](WAAM_Validator_입력물_인터페이스.md),
모든 설정 필드는 [Config 참조](docs/config-reference.md)를 확인하십시오.

### 3. 입력 검사와 Validation

```powershell
waam-validator check .\my_job
waam-validator run .\my_job
```

`check`는 파일·schema·trajectory 의미와 Target 좌표 일관성을 검사하지만 충돌,
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
3. **결과** — 종합, 로봇·Reach, 충돌, 형상, 산출물 탭 확인

Replay는 기본 생성하지 않습니다. Validation이 끝난 뒤 `산출물` 탭에서 frame
간격과 예상 시간·용량을 확인하고 필요할 때만 생성할 수 있습니다.

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
| `collision_events.csv` | 충돌 종류, robot pair, 발생 시간과 위치 |
| `layer_metrics.csv` | layer별 면적, Coverage, Underfill, Overfill, IoU |
| `warnings.csv` | warning과 정상 FAIL 원인이 된 비치명 이슈 |
| `deposited.stl` | 경로와 명목 비드 설정으로 재구성한 적층 형상 |
| `run.log` | 입력, 일정, 충돌, 형상 및 판정 실행 로그 |
| `*.png` | XY overview, Gantt, layer 지표와 worst-layer 비교 |
| `replay.html` | 선택적으로 생성하는 self-contained 3D Replay |

각 지표의 분모와 해석, 전체 필드 목록은 [결과 및 산출물 참조](docs/results-reference.md)에
정리되어 있습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [문서 안내](docs/README.md) | 목적별 문서 찾기 |
| [설치 및 시작하기](docs/getting-started.md) | 환경 구성, 첫 실행, CLI 사용 패턴 |
| [입력물 인터페이스](WAAM_Validator_입력물_인터페이스.md) | 알고리즘이 생성해야 할 세 입력 파일의 상세 계약 |
| [Config 참조](docs/config-reference.md) | schema 1.1의 모든 설정 필드와 제약 |
| [검증 방법과 판정 기준](docs/validation-method.md) | 시간, Reach, 충돌, 적층·형상 알고리즘과 한계 |
| [UI 사용 안내](docs/ui-guide.md) | 입력 준비부터 결과·Replay 확인까지의 화면 흐름 |
| [결과 및 산출물 참조](docs/results-reference.md) | JSON/CSV/보고서, 종료 코드와 결과 해석 |
| [Python API](docs/python-api.md) | 공개 함수, 주요 데이터 타입과 진행 콜백 |
| [개발자 안내](docs/development.md) | 코드 구조, 테스트, 정적 검사와 기여 시 주의사항 |

## 설계 원칙

- 원본 입력 파일을 복사하거나 수정하지 않습니다.
- 같은 입력을 반복 실행해도 결과 파일과 event 순서를 결정론적으로 생성합니다.
- 시간 통계는 adaptive sample이 아닌 원본 interval에서 계산합니다.
- 시각화 preview의 downsampling은 판정 결과에 영향을 주지 않습니다.
- 실행 결과는 매번 새 폴더에 저장해 이전 결과를 덮어쓰지 않습니다.
- UI는 기본적으로 `127.0.0.1`에만 열리며 인증·외부 배포 기능을 제공하지 않습니다.

## 범위 밖의 항목

현재 구현은 실제 로봇 관절 자세, 링크 간 3D 충돌, 지그·설비·환경 충돌, 경로
생성·수정, 열전달, 용융풀, 잔류응력과 공정 안정성을 다루지 않습니다. 따라서
`PASS`는 이 저장소가 구현한 기하·일정 기준을 만족한다는 뜻이며, 실제 장비 운전의
안전이나 제작 품질을 보증하지 않습니다.
