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
| 전체 pytest 및 CLI | 테스트 통과, 골든 불일치 0, exit 0 | 통과 | 통과 |
| 실행기 점검 | check-only: 34건·후보 20건, exit 0 | 통과 | 통과 |
| 화면 | 샘플 집계·T027 산식·근거·다운로드 확인 | 미검증 (AppTest 집계만 통과) | 통과 |
| 잘못된 입력 | CRITICAL 중단, 이전 결과 미표시 | 미검증 (단위 테스트만 통과) | 통과 |
| Excel CSV | CP949·CRLF 저장본 업로드 시 UTF-8과 같은 결과 | 미검증 (단위 테스트만 통과) | 통과 (코드 페이지 949, §5 참조) |
| 종료·재실행 | Ctrl+C 종료 후 같은 포트로 재실행 가능, 중복 실행 시 안내 후 exit 2 | 통과 | 통과 |
| 오프라인 | 네트워크 해제 후 화면·분석·다운로드 가능 | 미검증 | 통과 |
| USB 복사본 | 새 경로에서 오프라인 의존성 설치·실행 성공 | 통과 | 통과 |

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

### 2026-10-10 · Windows 11 Pro 10.0.26300 (x64, ko-KR) · Python 3.12.10 · Streamlit 1.65.0

- 커밋 `9ed86f0` 깨끗한 클론, Python 3.12 가상환경에서 전체 pytest 75 통과·1 건너뜀
  (실행 권한 비트 시험, Windows 해당 없음). CLI exit 0·골든 불일치 0. check-only 34건·후보 20건.
- `start-windows.cmd` 더블클릭: 실행 창·브라우저 한글 정상, 127.0.0.1에만 바인딩.
  집계 34·20·3·2,634,000,000원, T027 산식(130,000,000 − 100,000,000 → 30,000,000) 공시,
  거래 상세의 사실·미확인 사항·당사자·링크 확인. 사건 필터와 관계없이 JSON에 2개 사건·34건 전체 저장.
- 잘못된 CSV(T001 거래일 `2025-13-45`) 업로드: CRITICAL 사유 표시 후 중단, 직전 업로드 결과 미표시.
- 중복 실행: 같은 포트 실행 시 안내 후 exit 2. Ctrl+C 후 같은 포트로 즉시 재실행 성공.
  다만 재실행한 서버 2개가 1~2분 안에 종료된 일이 있었고(충돌 기록 없음), 원인은 확인하지 못했다.
  세 번째 재실행은 30분 이상 유지됐다.
- Excel CSV: 이 PC는 시스템 코드 페이지가 65001('Beta: Unicode UTF-8 사용')이라 Excel의
  'CSV (쉼표로 분리)' 저장본이 CP949가 아니라 BOM 없는 UTF-8·CRLF로 저장됐다. 이 저장본 업로드는
  샘플과 같은 결과. CP949 저장본은 같은 PC를 코드 페이지 949로 바꿔 아래 기록에서 확인했다.
- 오프라인: Wi-Fi 해제(WLAN 이벤트 로그로 확인) 상태에서 화면·분석·JSON 다운로드 성공.
- USB: `scripts/build_usb.py`로 `trustee-fds-Windows-AMD64-py312.zip` 생성(wheel 38개,
  `working_tree_dirty: false`). 네트워크 해제 상태에서 새 경로에 압축 해제 후 `--no-index` 설치,
  check-only 34건·후보 20건, 8502 포트 화면 확인.
- 관찰 (합격 기준 외, 후속 과제):
  - 브라우저 번역(이 PC에 설치된 번역 확장 사용 시)을 켜면 경고문 뜻과 JSON 입력 값까지 바뀌어 보인다.
    페이지가 영어(`lang="en"`)로 표시되어 번역 대상이 된다. 사용 시 번역을 끄고, 언어 지정·번역 차단은 후속 과제.
  - `data/sample` CSV는 BOM 없는 UTF-8이라 한국어 Excel에서 더블클릭으로 열면 한글이 깨진다.
  - 샘플 비고·`contrary_evidence`의 '—'(U+2014)는 CP949로 표현되지 않아 CP949 저장 시 '?'로 바뀐다.
    판정 결과에는 영향이 없었다. 이후 샘플 입력 CSV의 '—'를 '-'로 바꾸고, CP949로 표현되지 않는
    문자가 다시 들어오면 테스트가 실패하도록 했다.
  - T027 산식 문구가 130000000원·100000000원(쉼표 없음)과 30,000,000원(쉼표)을 섞어 표기한다.
  - 우측 상단 Streamlit 'Deploy' 버튼이 노출된다.

### 2026-10-11 · 같은 Windows PC, 시스템 코드 페이지 949 (일반 한국어 Windows 기본값)

'Beta: Unicode UTF-8 사용'을 끄고 재부팅해 ACP·OEMCP 949, Python 기본 인코딩 cp949 상태에서 반복했다.

- 전체 pytest 75 통과·1 건너뜀, CLI exit 0·골든 불일치 0, check-only 34건·후보 20건.
- `start-windows.cmd`로 띄운 현재 버전 브라우저 집계 34·20·3·2,634,000,000원.
  실행 창 한글 표시는 이전 검증 폴더의 실행기 창으로만 확인했다(아래 주의 참조).
- Excel CSV: 한국어 Excel에서 'CSV (쉼표로 분리)'로 저장한 5개 파일이 CP949·CRLF임을 바이트로 확인,
  업로드 결과가 샘플과 같았다. 비고 열의 '—' 9곳은 Excel 저장 시 '?'로 바뀌었으나 판정은 같았다(이후 샘플에서 '-'로 교체).
- USB: 같은 ZIP을 새 경로에 압축 해제, `--no-index` 설치 exit 0, check-only 34건·후보 20건,
  8502 포트 화면 집계 동일. 8501 사용 중 실행 시 한국어 안내 후 exit 2.
- 주의: 이 PC에는 2026-09-08에 만든 이전 검증 폴더(`57a903d`)가 남아 있어 그 실행기가 먼저 실행된 일이 있었다.
  이전 버전은 포트 사용 중 안내 없이 Streamlit 오류로 끝난다. 배포 PC에는 한 버전만 둔다.

독립 실행파일(EXE/APP)이 필요하면 다음 단계에서 OS별 패키징과 깨끗한 PC 검증을 수행한다.
