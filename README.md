# WAAM Validator

`WAAM Validator`는 세 대의 로봇으로 수행하는 WAAM 경로 계획을 동일한 기준으로
검증하는 Python 3.11+ CLI 및 라이브러리입니다. 각 로봇은 World XY 평면의
Base–TCP 선분과 TCP 허용 원으로 단순화되며, `D` 구간은 고정 폭·높이의 명목
비드 형상으로 변환됩니다.

## 설치

```powershell
cd C:\Github\WAAM_Validator
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

## 입력

작업 디렉터리에는 이름이 고정된 세 파일이 필요합니다.

```text
sample_job/
├── config.yaml
├── trajectory.csv
└── target.stl
```

CSV 헤더는 정확히 다음과 같아야 합니다.

```csv
robot_id,time_s,x_mm,y_mm,z_mm,mode
```

## 실행

```powershell
waam-validator check .\sample_job
waam-validator run .\sample_job
waam-validator run .\sample_job --headless
waam-validator run .\sample_job --json
```

기본 결과는 `sample_job/output/<timestamp>/`에 보존됩니다. `--output`을 지정한
경우 해당 디렉터리가 비어 있거나 아직 존재하지 않아야 합니다.

## 판정의 한계

이 도구는 실제 로봇 링크·관절·Z 방향 분리·환경 충돌을 모델링하지 않습니다.
형상 결과 역시 일정한 비드 폭과 layer 높이에 기반한 명목 기하 비교이며,
열변형·용융풀·잔류응력 또는 실제 공정 품질을 예측하지 않습니다.
