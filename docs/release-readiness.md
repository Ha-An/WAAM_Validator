# 릴리스 준비 및 검증 기록

이 문서는 WAAM Validator `1.0`의 GitHub 업로드 전 점검 결과를 기록합니다. 수치는
2026-09-06에 현재 작업 트리와 Python 3.11 Windows 가상환경에서 다시 측정했습니다.
`tests/**/output/`은 Git에 포함되지 않으므로 아래 결과는 재현 증거이지 배포 파일은
아닙니다.

## 결론

- 애플리케이션 버전 `1.0`, 결과 schema `2.0`, Config 버전 필드 없음이 코드·문서와 일치합니다.
- 추적 중인 Config 21개와 Validator 입력 작업 20개가 모두 구조 검사를 통과했습니다.
- 최신 schema 2.0 결과 12개에서 시간 합계, 충돌 이벤트 수, Layer 실패 수, 안전 여유,
  Coverage 관계와 최종 PASS/FAIL의 내부 정합성이 모두 확인됐습니다.
- Motor는 `PASS`, Twisted Half는 예상대로 Robot Reach 위반만으로 `FAIL`입니다.
- 10만 행 성능 시험은 peak RSS `201.36 MiB`로 512 MiB 기준을 충족했습니다.
- 일반 테스트는 `129 passed, 2 skipped, 1 performance deselected`, 별도 성능 테스트는
  `1 passed`였습니다. Ruff, strict mypy, `pip check`, Markdown 내부 링크 검사도
  통과했습니다.

## 실제 모델 재검증

두 대형 입력은 현재 Capsule 코드와 결과 schema 2.0으로 headless 전체 파이프라인을
다시 실행했습니다. 실행시간은 해당 `run.log`의 첫 기록부터 완료 기록까지입니다.

| 작업 | 결과 run | 상태 | 행 | Makespan | 실행시간 | Arm 중심선/요구/여유 [mm] | Coverage | IoU |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| Motor | `2026-09-06_173242` | PASS | 65,748 | 99,891.464 s | 105.31 s | 382.029 / 200.000 / +182.029 | 99.9743% | 99.9739% |
| Twisted Half | `2026-09-06_173443` | FAIL | 744,659 | 405,716.444 s | 177.81 s | 623.270 / 250.000 / +373.270 | 99.9994% | 99.9993% |

Motor는 Reach, Arm Envelope, TCP Radius와 형상 판정을 모두 통과했습니다. Twisted
Half는 세 로봇 모두 최대 Reach 약 2,417.6 mm로 설정값 2,000 mm를 넘었습니다.
Arm Envelope event와 TCP Radius event는 0건이고 형상 판정도 PASS이므로 전체 FAIL의
원인은 Reach로 한정됩니다.

10개 절차적 모델의 세부 입력과 기록된 headless 측정값은
[benchmark 결과](../tests/benchmark_results.md)에 별도로 보존합니다. 이 모델들은
Target과 trajectory가 같은 centerline에서 생성된 positive consistency test이므로
Coverage·IoU가 거의 100%인 것이 정상입니다. 독립 경로 생성기의 일반적인 형상 정확도를
입증하는 자료로 해석하면 안 됩니다.

## 과거 로그 감사

로컬 `tests/**/output/`에서 확인한 `run.log` 57개 가운데 traceback이 기록된 파일은
다음 구형 실행 한 건입니다.

```text
tests/twisted_half/output/2026-08-25_235109/run.log
TypeError: 'numpy.ndarray' object does not support the context manager protocol
```

이 로그는 제거된 구형 선분 교차 함수 `check_arm_crossing_xy`에서 발생했습니다. 현재
구현은 batch 2D Capsule 거리 계산으로 교체됐고, 같은 Twisted 입력의 schema 2.0 전체
재검증이 완료되어 재현되지 않았습니다.

Motor의 `2026-09-04_231532` 폴더처럼 worker가 중단되어 `dashboard_status.json`만 남은
미완성 결과도 로컬에 있을 수 있습니다. 결과 로더는 `summary.json` 또는 `error.json`이
없는 디렉터리를 완료 결과로 선택하지 않습니다. 이 폴더들은 입력이나 최신 완료 결과를
손상하지 않지만 필요하면 사용자가 확인 후 직접 정리할 수 있습니다.

정상 `FAIL` 결과의 `errors`와 `warnings.csv`에 있는 `severity=error`는 계산이 끝난
판정 위반의 상세 기록입니다. 파이프라인이 중단된 `status=ERROR`와 다릅니다. UI에서는
결정론적인 FAIL 사유를 먼저 표시하고 중복되는 상세 기록은 펼침 영역에 둡니다.

## 이번 감사에서 수정한 항목

- `warnings.csv`가 없거나 손상됐을 때 `summary.json.errors` fallback이 화면에서
  누락되던 문제를 수정했습니다.
- 정상 FAIL의 같은 위반이 FAIL 사유와 상세 목록에 연속으로 반복되던 화면을 펼침형
  세부 판정 기록으로 정리했습니다.
- 보고서와 로그의 `errors` 표현을 `error-severity validation issues`로 바꿔 치명적
  실행 `ERROR`와 구분했습니다. JSON field와 CSV schema는 호환성을 위해 유지합니다.
- CI가 `pyproject.toml`에서 transitive dependency를 새로 해석하던 구성을 고쳐
  `requirements.lock`과 로컬 환경의 67개 package가 정확히 일치하도록 했습니다.
- “모든 결과 파일이 결정론적”이라는 과도한 문구를 핵심 수치·event·CSV 순서로
  한정했습니다. 출력 경로와 로그 timestamp는 실행마다 달라지는 것이 정상입니다.
- benchmark 표의 run을 최신 폴더로 오해할 수 있던 표현을 정확한 headless 측정 run으로
  고쳤고, 실제 모델의 참조 STL과 canonical `target.stl` 차이를 문서화했습니다.

## 성능 확인

```text
rows=100000
peak_rss_mib=201.36
full_pipeline_elapsed_s=3.13
capsule_batch_100k_s=0.0331
```

이 값은 현재 검사 장비에서의 참고 측정치입니다. CPU, 저장장치, trajectory의 adaptive
분할 수와 STL 복잡도에 따라 실제 시간은 달라집니다. Motor와 Twisted가 행 수보다 오래
걸리는 주된 이유는 각각 약 258만, 592만 개의 adaptive 충돌 샘플을 검사하기 때문입니다.

## Git 저장소 점검

- `.gitignore`는 `.venv`, cache, coverage, build와 모든 `output/`을 제외합니다.
- 로그, 결과 JSON/CSV, PNG, Replay HTML과 생성된 `deposited.stl`은 추적하지 않습니다.
- 재현에 필요한 입력 Config·trajectory·Target과 benchmark 요약만 추적합니다.
- 가장 큰 추적 파일은 `tests/twisted_half/trajectory.csv` 약 46.24 MiB로 GitHub의
  단일 파일 100 MiB 제한보다 작습니다.
- 실제 모델 폴더에서 Validator가 읽는 형상은 `target.stl`입니다. 추가 이름의 STL은
  참조 사본이며, 특히 Motor 원본은 canonical Target과 좌표·스케일이 다릅니다.
- `.gitattributes`는 텍스트를 LF로 통일하고 STL·이미지·영상은 binary로 취급합니다.
- 현재 저장소에는 License 파일이 없습니다. 공개 배포 시 제3자 사용 조건이 필요하면
  별도의 라이선스 결정을 먼저 해야 합니다.

## 릴리스 전 재현 명령

```powershell
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python scripts/generate_fixtures.py

ruff check .
mypy
python -m pip check
pytest -q
pytest -q -m performance -s
git diff --check
```

대표 입력 smoke test:

```powershell
waam-validator check .\examples\sample_job
waam-validator run .\examples\sample_job --headless --json
waam-validator ui .\examples\sample_job --no-browser
```

CI는 `requirements.lock`을 설치한 뒤 editable package를 `--no-deps`로 연결하므로 로컬
품질 검사와 같은 해석 의존성을 사용합니다.

## 남은 비차단 참고사항

- Windows에서 symlink 생성 권한이 없어 path escape 관련 테스트 2개가 skip됐습니다.
  같은 테스트는 symlink를 지원하는 CI 운영체제에서 실행됩니다.
- Dash 4.4.1은 내장 `dash_table.DataTable`의 향후 제거를 알리는 deprecation warning을
  출력합니다. 현재 고정 버전에서는 정상 동작하지만 다음 Dash major 업그레이드 전에
  `dash-ag-grid` 등으로 이전할 필요가 있습니다.
- 배포 wheel `waam_validator-1.0-py3-none-any.whl` 생성과 두 UI CSS package-data 포함을
  확인했습니다. 감사용 wheel은 임시 폴더에서만 만들고 저장소에는 남기지 않았습니다.

[문서 안내로 돌아가기](README.md)
