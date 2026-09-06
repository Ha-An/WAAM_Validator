# WAAM Validator 문서

이 폴더는 루트 [README](../README.md)의 빠른 시작보다 상세한 운용·판정·개발
정보를 제공합니다.

## 목적별 문서

| 알고 싶은 내용 | 문서 |
| --- | --- |
| 설치하고 예제 또는 실제 작업을 처음 실행하는 방법 | [설치 및 시작하기](getting-started.md) |
| 알고리즘 결과를 Validator 입력으로 만드는 정확한 규격 | [입력물 인터페이스](../WAAM_Validator_입력물_인터페이스.md) |
| `config.yaml`의 필드와 유효 범위 | [Config 참조](config-reference.md) |
| PASS/FAIL이 어떤 계산을 거쳐 결정되는지 | [검증 방법과 판정 기준](validation-method.md) |
| 로컬 UI에서 입력 확인, 실행, 결과와 Replay를 보는 방법 | [UI 사용 안내](ui-guide.md) |
| `summary.json`, CSV, STL, PNG와 종료 코드를 해석하는 방법 | [결과 및 산출물 참조](results-reference.md) |
| Python 코드에서 Validator를 호출하는 방법 | [Python API](python-api.md) |
| 저장소 구조와 테스트·품질 검사를 실행하는 방법 | [개발자 안내](development.md) |
| 애플리케이션 버전과 Config·결과 호환성의 차이 | [버전 및 호환성](versioning.md) |
| 10개 절차적 모델의 입력 생성 방법과 실제 Validation 결과 | [10개 모델 benchmark 결과](../tests/benchmark_results.md) |

## 권장 읽기 순서

- **사용자:** 시작하기 → 입력물 인터페이스 → UI 사용 안내 → 결과 참조
- **경로 생성 알고리즘 개발자:** 입력물 인터페이스 → Config 참조 → 검증 방법
- **자동화 개발자:** 시작하기 → 결과 참조 → Python API
- **프로젝트 기여자:** 검증 방법 → Python API → 버전 및 호환성 → 개발자 안내

문서와 실제 코드가 다르게 보이면 `src/waam_validator`의 Config 모델과 테스트가 최종
동작 기준입니다. 차이를 발견하면 README와 관련 문서도 코드 변경과 함께 갱신해야
합니다.
