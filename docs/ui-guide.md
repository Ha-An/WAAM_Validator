# UI 사용 안내

WAAM Validator UI는 여러 작업을 관리하는 대시보드가 아니라 **한 작업의 입력 확인,
Validation, 결과 해석을 끝까지 이어 가는 로컬 인터페이스**입니다.

## 실행

```powershell
waam-validator ui C:\data\my_job
```

`JOB_DIR`은 화면의 폴더 입력란에 미리 채워집니다. 기본 브라우저가
`http://127.0.0.1:8050`을 열며 다음 옵션을 사용할 수 있습니다.

```powershell
waam-validator ui C:\data\my_job --port 8051
waam-validator ui C:\data\my_job --no-browser
```

기본 host는 loopback인 `127.0.0.1`입니다. 이 UI에는 인증이 없으므로 신뢰할 수
없는 네트워크에 공개하는 용도로 사용하지 마십시오.

## 화면 1: 입력 준비

### 폴더 지정

세 입력 파일이 함께 있는 폴더 하나를 지정합니다.

```text
my_job/
├── config.yaml
├── trajectory.csv
└── target.stl
```

Windows에서는 `찾아보기`를 사용하거나 경로를 직접 붙여 넣을 수 있습니다. 폴더를
바꾼 뒤에는 반드시 `입력 확인`을 다시 누릅니다. 원본 파일은 복사하거나 덮어쓰지
않습니다.

### 입력 확인

`입력 확인`은 버튼을 누른 시점에 세 파일을 **한 번만** 읽습니다. 유휴 상태에서
주기적으로 trajectory나 STL을 다시 읽지 않습니다. 이 단계에서는 다음을 확인하고
preview를 만듭니다.

- 파일 절대 경로, 크기, 수정 시각
- Config 필드·타입과 로봇·공정·workspace·threshold
- Trajectory 전체 행 수, makespan, 로봇별 시간·XYZ 범위, D/T/W 시간·거리·속도
- Target의 정점·면·body 수, 경계·치수·체적·watertight 상태
- Trajectory 의미, Reach 예상 위반, Target/trajectory 좌표 일관성

충돌 시뮬레이션, Target layer slicing, 전체 형상 비교와 결과 산출물 생성은 이때
실행하지 않습니다.

### 입력 상태

| 상태 | 의미 | Validation 버튼 |
| --- | --- | --- |
| `READY` | 검사 항목에 문제가 없음 | 활성화 |
| `WARNING` | 비치명 이슈가 있으나 실행 가능 | 활성화 |
| `EXPECTED_FAIL` | Reach·Wait·설정된 속도 기준 등 정상 FAIL 예상 | 활성화 |
| `BLOCKED` | 파싱·Config·좌표·Deposition layer 등 치명 오류 | 비활성화 |

`EXPECTED_FAIL`은 실행 오류가 아닙니다. 전체 계산과 결과 저장이 가능하지만 최종
상태가 FAIL일 가능성이 있다는 의미입니다.

입력 확인 시 파일별 크기와 수정 시각으로 signature를 만듭니다. 이후 파일이
변경되거나 폴더 경로가 바뀌면 기존 검사 context를 무효화하고 Validation을
차단합니다. 최신 내용을 기준으로 `입력 확인`을 다시 실행하십시오.

### Preview 해석

#### 3D 작업 공간

- Target STL
- Robot Base와 Base XY 삼각형
- 설정한 경우, Base와 구분되는 명목 Home TCP 위치
- 각 Base 중심의 반투명 3D Reach 구
- World XY의 원형 workspace
- 로봇별 Deposition, Travel, Wait trajectory

브라우저 응답성을 위해 Target preview는 최대 50,000 face, trajectory는 로봇당
최대 10,000점으로 제한합니다. 시작·종료와 mode 전환점을 우선 보존하고, Target
단순화가 실패하면 결정론적 face sampling을 사용합니다. 이 제한은 **화면 표시만**
위한 것이며 Validation은 원본 전체 데이터를 사용합니다.

#### 작업 Gantt

R1/R2/R3을 각각 하나의 가로 막대로 표시하고, 원본 interval에서 연속된 같은 mode를
하나의 run으로 합쳐 실제 시작·종료 시간에 맞춰 색상을 전환합니다. Hover에서 각
상태의 시작, 종료와 지속시간을 확인할 수 있습니다.

- Deposition: 재료를 적층하며 이동하는 구간
- Travel: 적층하지 않고 다음 위치로 이동하는 구간
- Wait: trajectory가 지정한 위치를 유지하며 대기하는 구간
- 완료 후 비활성: 해당 로봇의 trajectory 종료부터 전체 makespan까지

네 상태는 시간축에서 서로 겹치지 않습니다. 모든 로봇의 첫 timestamp는 0이므로
별도의 `작업 전 비활성` 상태는 현재 입력 schema에 필요하지 않습니다.

수십 시간짜리 작업을 전체 축에 한 번에 표시하면 짧은 D/T 상태가 화면의 1픽셀보다
작아질 수 있습니다. 그래서 처음에는 세 로봇의 실제 상태 전환이 보이는 구간을 자동
확대합니다. 하단 범위 조절기를 끌거나 `상세 보기`, `전체 보기` 버튼을 사용해 확대
구간과 전체 timeline 사이를 전환할 수 있습니다.

#### 로봇별 상태 시간 비율

공통 makespan을 100%로 놓고 각 로봇의 Deposition/Travel/Wait 시간을 표시합니다.
각 로봇이 makespan보다 먼저 끝났다면 남은 부분은 Wait가 아니라
`완료 후 비활성`입니다.
그래프에 기준 makespan의 초·분·시간 환산값을 함께 표시합니다.

#### 로봇별 경로 길이

Deposition과 Travel interval의 3D 누적 TCP 거리입니다. 시간이 짧은 Travel이라도
설정 속도가 Deposition보다 크면 Travel 거리가 더 길 수 있습니다. 정확한 값은
trajectory 정보와 hover에서 확인합니다.

#### 로봇별 Reach 사용률

`Base에서 가장 먼 TCP까지의 3D 거리 / 설정 Reach 반경`입니다. 100%는 설정 한계,
100% 초과는 Reach FAIL을 뜻합니다. 경로 길이와는 다른 지표입니다.

## 화면 2: Validation 진행

입력 상태가 실행 가능하고 signature가 그대로이면 `Validation 실행`이 활성화됩니다.
누르면 입력 확인에서 만든 동일한 canonical 작업 context를 별도 Python process에
직접 전달합니다.

진행 화면에는 다음을 표시합니다.

- 전체 진행률과 현재 단계 진행률
- 처리한 simulation 시간, interval 또는 layer 수
- 경과 시간과 실측 속도 기반 예상 잔여 시간
- 제한된 최신 실행 로그

단계별 전체 진행률 구간은 다음과 같습니다.

| 단계 | 전체 진행률 |
| --- | --- |
| 입력 로딩 | 0–5% |
| 충돌 검사 | 5–45% |
| 적층 layer 형상 | 45–60% |
| Target slicing | 60–85% |
| 형상 지표 | 85–90% |
| 산출물 | 90–100% |

UI는 실행 중에만 상태 파일의 작은 진행 정보와 제한된 log tail을 1초 간격으로
확인합니다. 이 과정에서 trajectory, STL, 결과 CSV나 그래프를 다시 읽지 않습니다.
프로세스가 끝나면 polling을 즉시 끄고 정확히 그 process의 output 폴더로 결과
화면을 한 번 갱신합니다. 중단 버튼은 제공하지 않습니다.

## 화면 3: 결과

### 종합

PASS/FAIL, makespan, 충돌 event 수, 최소 TCP 거리, Coverage, IoU, 실패 layer 수와
Failure Reasons를 우선 확인합니다. Warning은 결과를 무조건 FAIL로 만들지 않으므로
Failure Reasons와 구분해 읽어야 합니다.

### 로봇 · Reach

로봇별 completion, Deposition/Travel/Wait 시간·비율, 적층·이동 거리, 평균 속도와 Reach 반경,
최대 사용 거리, margin, 사용률, 위반 절점 수를 확인합니다.

### 충돌

ARM_CROSS와 TCP_RADIUS event의 robot pair, 시작·종료·지속 시간과 최소 거리를
확인합니다. Event가 없더라도 전체 최소 TCP 거리와 요구 거리를 비교할 수 있습니다.

### 형상

전체 Coverage, Underfill, Overfill, IoU와 실패 layer 비율을 설정 threshold와
비교합니다. 상세 표에서 layer별 target/deposition/intersection 면적과 IoU를
확인하고 worst-layer PNG를 함께 봅니다.

### 산출물

보고서, JSON/CSV, 로그, PNG와 `deposited.stl`을 내려받거나 열 수 있습니다.
시각화가 꺼진 headless 결과는 해당 영역만 비어 있고 나머지 지표는 정상 표시됩니다.

## Replay를 나중에 생성하기

기본 Validation에는 `replay.html`이 없습니다. 산출물 탭에서 다음 순서로 만듭니다.

1. 빠른 확인, 권장, 상세 preset 또는 직접 frame 간격을 선택합니다.
2. 예상 frame 수, 생성 시간 범위와 파일 크기 범위를 확인합니다.
3. `Replay 생성`을 누릅니다.
4. 별도 process가 끝나면 같은 탭에서 Replay를 지연 로딩합니다.

일반 frame 상한은 2,000개입니다. 매우 긴 작업에서는 몇 초 이상의 간격이 권장될
수 있습니다. Replay 생성 전에도 입력 signature를 다시 확인하므로 Validation 이후
원본 세 파일이 바뀌었다면 기존 결과에 Replay를 붙이지 않습니다.

## 결과 이후 동작

- `같은 입력 다시 실행`: 세 입력 signature를 다시 확인한 후 새 output 폴더에서 실행
- `다른 입력 선택`: 입력 준비 화면으로 돌아가 새 폴더 지정
- `최근 결과 보기`: 현재 입력 signature와 지원하는 결과 schema가 일치하는 완료 결과가 있을 때만 표시

기존 결과 폴더는 자동 삭제하거나 덮어쓰지 않습니다.

## 문제 해결

### 입력 확인 버튼을 눌러도 진행되지 않는 것처럼 보임

큰 STL을 읽고 preview를 만드는 동안 버튼에 loading 상태가 표시됩니다. 완료 후에도
변화가 없다면 화면의 입력 오류 카드와 서버 콘솔을 확인하고, 세 고정 파일명이
정확한지 점검합니다.

### Validation 버튼이 비활성화됨

- 입력 확인 결과가 `BLOCKED`인지 확인합니다.
- 폴더 입력값을 검사 후 수정하지 않았는지 확인합니다.
- 파일을 생성 프로그램이 다시 저장해 size/mtime이 바뀌지 않았는지 확인합니다.
- `입력 확인`을 다시 실행합니다.

### Validation 버튼을 눌러도 화면이 바뀌지 않음

서버 콘솔과 입력 카드의 실행 오류를 확인합니다. 이미 Validation 또는 Replay
process가 실행 중이면 동시에 새 Validation을 시작하지 않습니다. UI를 다시 띄울
때는 기존 port가 사용 중인지 확인하고 다른 `--port`를 지정할 수 있습니다.

### 결과에 Replay가 없음

정상 동작입니다. 산출물 탭에서 생성하거나 CLI에 `--replay`를 명시하십시오.

[문서 안내로 돌아가기](README.md)
