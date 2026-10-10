# trustee-fds 로컬 실행 및 USB 배포

이 구성은 Python이 설치된 PC를 위한 **소스 배포본**이다. Python 내장 EXE/APP,
서명된 설치 프로그램, 사용자 인증·권한 관리가 포함된 제품은 아니다.
코어 엔진과 YAML을 그대로 호출하며, 현재 연구·검증용 제한을 유지한다.

## 1. 최초 설치 (인터넷 연결)

UI 배포의 기준 환경은 **Python 3.12**다. 기존 CLI의 Python 지원 범위는 그대로다.
저장소 폴더에서 실행한다.

macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-ui.txt -r requirements-dev.txt -c constraints-ui.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m src.onprem --check-only
bash start-macos.command
```

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt -r requirements-dev.txt -c constraints-ui.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.onprem --check-only
.\start-windows.cmd
```

브라우저에서 http://127.0.0.1:8501 을 연다. 포트가 이미 사용 중이면 실행기가 시작 전에
알리고 종료한다(exit 2). 이때 가상환경 Python으로 `-m src.onprem --port 8502`를 실행한다.
종료는 실행 창에서 Ctrl+C.

`constraints-ui.txt`는 Streamlit과 하위 의존성 버전을 고정한다. 버전을 올릴 때는 파일 머리의
갱신 절차를 따르고, 전체 pytest와 아래 합격 기준을 다시 확인한다.
실행기는 loopback에만 바인딩하고 사용 통계 전송을 끈다. 네트워크 차단을 강제하는
방화벽은 아니며, 완전 오프라인 실행은 아래 실기기 시험으로 확인한다.

## 2. 사용

1. 합성 샘플 선택 → 분석 실행.
2. 전체 34건, 후보 20건, 평가 필요 3건, 평가 완료분 순출연 추정액 2,634,000,000원 확인.
3. 사건 선택, 거래 상세에서 사실·미확인 사항·당사자·링크 확인.
4. T027의 실제 지급액 130,000,000원, 채무 감소 100,000,000원,
   초과분 30,000,000원 공시 확인.
5. 전체 사건 결과와 입력 근거를 JSON으로 다운로드. 사건 필터와 관계없이 전체를 저장한다.

CSV 업로드는 `data/sample`과 같은 열을 가진 파일을 사용한다. UTF-8(BOM 포함)과
한국어 Windows Excel의 기본 저장 형식(CP949)을 받는다. 둘 다 아니면 'CSV UTF-8'로
다시 저장하라는 안내와 함께 거부한다.
cases, transactions, parties, transaction_parties는 필수이며 transaction_links는 선택이다.
입력을 바꾸면 기존 결과를 폐기한다. CRITICAL은 결과 생성 전에 중단한다.
업로드 자료는 일시 디렉터리에서 읽은 뒤 삭제하고, 결과는 해당 브라우저 세션에서 유지한다.
JSON 저장본에는 입력 사실이 포함된다. 자동 DB 저장·세션 복원 기능은 없다.

## 3. USB용 오프라인 소스 배포본 생성

**각 대상 OS·CPU 아키텍처·Python 3.12 환경에서 따로** 실행한다.
Mac에서 만든 의존성 묶음을 Windows에서 사용하거나 가상환경 폴더를 복사하지 않는다.

```bash
# macOS
.venv/bin/python scripts/build_usb.py
```

```powershell
# Windows
.\.venv\Scripts\python.exe scripts/build_usb.py
```

이 단계는 인터넷에서 현재 환경용 wheel을 내려받는다. 실패하면 완성본으로 취급하지 않는다.
`dist/trustee-fds-<OS>-<CPU>-py312.zip`을 USB에 복사한다.
ZIP은 소스·룰·합성 샘플·문서·wheelhouse·BUILD.json·SHA256.json을 포함한다.
BUILD.json에는 빌드한 git 커밋과 미커밋 변경 여부(`working_tree_dirty`)가 기록된다.
`working_tree_dirty`가 true인 묶음은 배포하지 않는다.
SHA256은 복사 손상 검사용이며 배포자 서명을 대신하지 않는다.

대상 PC에 같은 Python 마이너 버전과 호환 아키텍처가 있어야 한다.
인터넷이 없는 PC라면 Python 설치도 사전에 완료한다.
USB의 ZIP을 PC 로컬 폴더로 복사·압축 해제한 후 가상환경을 새로 만든다.

```bash
# macOS: 압축 해제한 폴더에서
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements-ui.txt -c constraints-ui.txt
bash start-macos.command
```

```powershell
# Windows: 압축 해제한 폴더에서
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements-ui.txt -c constraints-ui.txt
.\start-windows.cmd
```

## 4. 실제 기기 합격 기준

| 시험 | 성공 기준 | Mac | Windows |
|---|---|---|---|
| 전체 pytest 및 CLI | 테스트 통과, 골든 불일치 0, exit 0 | 통과 | 미검증 |
| 실행기 점검 | check-only: 34건·후보 20건, exit 0 | 통과 | 미검증 |
| 화면 | 샘플 집계·T027 산식·근거·다운로드 확인 | 미검증 (AppTest 집계만 통과) | 미검증 |
| 잘못된 입력 | CRITICAL 중단, 이전 결과 미표시 | 미검증 (단위 테스트만 통과) | 미검증 |
| Excel CSV | CP949·CRLF 저장본 업로드 시 UTF-8과 같은 결과 | 미검증 (단위 테스트만 통과) | 미검증 |
| 종료·재실행 | Ctrl+C 종료 후 같은 포트로 재실행 가능, 중복 실행 시 안내 후 exit 2 | 통과 | 미검증 |
| 오프라인 | 네트워크 해제 후 화면·분석·다운로드 가능 | 미검증 | 미검증 |
| USB 복사본 | 새 경로에서 오프라인 의존성 설치·실행 성공 | 통과 | 미검증 |

GitHub CI는 Linux·Windows·Mac에서 UI/실행기 테스트를 돌린다.
CI 성공은 실제 사용자 기기의 브라우저·오프라인·USB 검증을 대신하지 않는다.

## 5. 확인 기록

### 2026-10-10 · macOS 26 (Apple Silicon) · Python 3.12.10 · Streamlit 1.65.0

- 깨끗한 Python 3.12 가상환경에서 전체 pytest 통과, CLI exit 0·골든 불일치 0.
- `start-macos.command` 실행: 127.0.0.1에만 바인딩, health 응답 정상.
  중지 신호(SIGINT) 후 같은 포트로 즉시 재실행 성공. 중복 실행 시 안내 후 exit 2.
- `scripts/build_usb.py`로 `trustee-fds-Darwin-arm64-py312.zip` 생성,
  새 경로에 압축 해제 후 `--no-index` 설치와 check-only 성공.
- 사람이 화면을 직접 조작하는 시험과 네트워크를 끈 상태의 시험은 아직 하지 않았다.

독립 실행파일(EXE/APP)이 필요하면 다음 단계에서 OS별 패키징과 깨끗한 PC 검증을 수행한다.
