# 검증 데이터 안내

이 디렉터리에는 자동화 테스트 코드와 실제 실행에 사용할 수 있는 Validator 입력
세트가 함께 있습니다. 각 작업에서 실제 Validator가 읽는 파일은 항상
`config.yaml`, `trajectory.csv`, `target.stl` 세 개입니다.

## 데이터 구성

| 경로 | 용도 |
| --- | --- |
| `fixtures/` | 충돌, Reach, underfill·overfill 등을 작게 격리한 회귀 입력 |
| `01/`–`10/` | 절차적으로 생성한 형상과 heuristic 3-Robot trajectory |
| `motor/` | Motor 작업의 canonical Validator 입력과 원본 참조 STL |
| `twisted_half/` | Twisted Half 작업의 canonical Validator 입력과 원본 참조 STL |
| `unit/` | 계산 단위 테스트 |
| `integration/` | CLI, pipeline과 UI 연결 테스트 |
| `performance/` | 10만 행 메모리·처리시간 수락 테스트 |

Motor와 Twisted 폴더의 `target.stl`만 형상 판정에 사용됩니다.
`motor_inner.stl`과 `twisted_tri_shell_1600_half.stl`은 모델 출처를 식별하기 위해 남긴
참조 사본입니다. 현재 `twisted_tri_shell_1600_half.stl`은 `target.stl`과 동일하지만,
`motor_inner.stl`은 원본 좌표·스케일이 달라 canonical `target.stl`과 동일 파일이
아닙니다. 파일을 서로 바꿔 사용하면 trajectory와 좌표계가 일치하지 않습니다.

## 생성 결과 정책

각 작업의 `output/`은 `.gitignore` 대상입니다. 과거 실행의 JSON, CSV, 로그,
Replay HTML과 `deposited.stl`은 로컬 진단에는 사용할 수 있지만 GitHub에는 올리지
않습니다. 재현 가능한 입력과 다음 요약만 추적합니다.

- [10개 모델 benchmark 결과](benchmark_results.md)
- [기계 판독용 benchmark 수치](benchmark_results.csv)

Worker가 중단되어 `summary.json`이나 `error.json`이 없는 output 폴더는 완료 결과가
아닙니다. UI의 최근 결과 선택에서도 자동으로 제외됩니다.

## 실행 예

```powershell
waam-validator check .\tests\08
waam-validator run .\tests\08 --json
waam-validator ui .\tests\08
```

[README로 돌아가기](../README.md)
