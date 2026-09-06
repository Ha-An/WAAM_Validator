# WAAM Validator 문서

루트 [README](../README.md)는 설치와 첫 실행만 간단히 설명합니다. 세부 내용은 목적에
맞는 문서를 사용하십시오.

| 목적 | 문서 |
| --- | --- |
| 설치하고 첫 작업 실행 | [설치 및 시작](getting-started.md) |
| 경로 생성 알고리즘의 출력 규격 확인 | [입력물 인터페이스](WAAM_Validator_입력물_인터페이스.md) |
| `config.yaml` 필드와 단위 확인 | [Config 참조](config-reference.md) |
| PASS/FAIL 계산 원리 확인 | [검증 방법](validation-method.md) |
| 입력 준비·진행·결과 화면 사용 | [UI 안내](ui-guide.md) |
| JSON/CSV/보고서 해석 | [결과 참조](results-reference.md) |
| Python에서 직접 호출 | [Python API](python-api.md) |
| 테스트·정적 검사·패키징 | [개발자 안내](development.md) |
| 앱 버전과 결과 schema 구분 | [버전 관리](versioning.md) |
| 포함된 검증 입력과 benchmark | [검증 데이터](../tests/README.md) |

권장 순서는 다음과 같습니다.

- 사용자: 설치 및 시작 → UI 안내 → 결과 참조
- 경로 생성 알고리즘 개발자: 입력물 인터페이스 → Config 참조 → 검증 방법
- 자동화 개발자: 결과 참조 → Python API
- 기여자: 검증 방법 → Python API → 개발자 안내 → 버전 관리

문서와 코드가 다를 경우 `src/waam_validator`의 Pydantic Config 모델, 결과 writer와
자동 테스트가 최종 동작 기준입니다.
