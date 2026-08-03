# 국립공원 실무 AI 지식 플랫폼

> 해외 국립공원 관리·연구 관련 학술논문을 AI가 자동으로 한글 번역·분석하여
> 국립공원 실무자가 **논문을 읽지 않아도 현장에 바로 적용**할 수 있는 정보를 제공합니다.

## 아키텍처

```
OpenAlex API ──▶ collector.py ──▶ raw_papers.json
                                        │
                    Gemini API ──▶ enricher.py  (초록 한글 번역 + AI 실무 분석)
                                        │
                               indexer.py ──▶ papers.json
                                                  │
                                         index.html (GitHub Pages)
```

## 파일 구조

| 파일 | 역할 |
|------|------|
| `collector.py` | OpenAlex API에서 해외 국립공원 관련 논문 수집 |
| `enricher.py` | Gemini API로 초록 한글 번역 + AI 실무 분석 생성 |
| `indexer.py` | papers.json 인덱스 빌드 |
| `run_pipeline.py` | 세 단계 한 번에 실행 |
| `index.html` | 프론트엔드 (GitHub Pages) |
| `papers.json` | 최종 데이터 (자동 생성) |
| `raw_papers.json` | 수집 원본 데이터 (자동 생성) |
| `enrich_state.json` | 일일 AI 요청 사용량 기록 (자동 생성) |
| `progress.json` | 검색어별 수집 완료 체크포인트 (자동 생성) |

## 배포 방법

### 검색어 체크포인트 (progress.json)
`collector.py`는 검색어를 하나씩 처리하면서, **검색어 텍스트 자체**를 기준으로 완료 여부와
실패 횟수를 `progress.json`에 기록합니다(순서·인덱스 기준이 아니라서 검색어를 추가하거나
순서를 바꿔도 안전합니다). 특정 검색어가 OpenAlex 429/시간초과로 계속 실패해도 **전체 실행을
멈추지 않고 다음 검색어로 넘어가며**, 매 실행마다 남은 검색어를 실패 횟수가 적은 순으로
정렬해서 처리합니다 — 아직 시도 안 한(또는 새로 추가된) 검색어가 항상 먼저 처리되고, 반복
실패하는 검색어는 자동으로 뒤로 밀립니다. 이 상황에서도 프로그램은 정상 종료(exit code 0)해서
Actions가 실패로 표시되지 않고, 그때까지 모은 `raw_papers.json`도 그대로 유지됩니다. 검색어
하나를 처리할 때마다 `raw_papers.json`을 즉시 저장하므로, 중간에 멈춰도 데이터 유실이 없습니다.

### 검색어를 코드 수정 없이 추가하기 (EXTRA_KEYWORDS)
`collector.py`의 `QUERIES` 목록을 직접 고치지 않고도 검색어를 추가할 수 있습니다.

- **한 번만 테스트해보고 싶을 때**: Actions 탭 → `Run workflow` 클릭 시 나오는
  `extra_keywords` 입력란에 쉼표로 구분해서 입력합니다.
  예: `national park drought impact, national park snow tourism`
- **계속 검색어로 남겨두고 싶을 때(스케줄 자동 실행에도 적용)**: 저장소
  Settings → Secrets and variables → Actions → **Variables** 탭 → New repository variable →
  이름 `EXTRA_KEYWORDS`, 값에 위와 같이 쉼표(또는 줄바꿈)로 구분해서 등록합니다.

`extra_keywords` 입력값을 채워서 수동 실행하면 그 값이 우선 적용되고, 비워두면 저장소
Variable(`EXTRA_KEYWORDS`)이 적용됩니다. 추가한 검색어도 동일하게 체크포인트가 적용되어,
한 번 완료되면 다시 검색하지 않습니다.

### 1. 저장소 생성 및 Push
```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```

### 2. GitHub Pages 활성화
Settings → Pages → Source: `main` 브랜치, `/ (root)`

### 3. Actions 쓰기 권한 활성화 (필수 — 이게 꺼져 있으면 커밋이 절대 안 쌓입니다)
Settings → Actions → General → 아래로 스크롤 → **Workflow permissions**에서
**"Read and write permissions"**를 선택하고 저장하세요. 저장소를 새로 만들면 기본값이
"Read repository contents permission"(읽기 전용)으로 되어 있는 경우가 많아서, 이 설정이
꺼진 채로 두면 Actions가 `papers.json`/`raw_papers.json`을 아무리 잘 분석해도 커밋·푸시를
못 하고 조용히(또는 워크플로 실패로) 끝나버립니다. 저장소 히스토리에 `chore: update papers`
커밋이 전혀 쌓이지 않는다면 거의 항상 이 설정이 원인입니다.

### 4. Secrets 등록
Settings → Secrets and variables → Actions → New repository secret

| Secret 이름 | 값 |
|------------|-----|
| `GEMINI_API_KEY` | Google Gemini API 키 (필수 — 초록 번역·AI 분석에 사용) |
| `OPENALEX_EMAIL` | 이메일 주소 (선택 — API 요청 속도 향상) |
| `LAW_API_OC` | 국가법령정보 Open API 계정 아이디 (선택 — 관련 법령 정확도 검증에 사용) |

> **국가법령정보 Open API 신청**: https://open.law.go.kr 에서 회원가입 후
> "OPEN API 활용신청"을 하면, 신청할 때 쓴 아이디가 그대로 `OC` 값입니다.
> 이 값을 `LAW_API_OC` 시크릿에 등록하면, AI가 생성한 "관련 법령" 목록을 실제
> 현행 법령 DB와 대조합니다. 실제 존재하는 법령이면 정확한 현행 법령명으로
> 정리하고, 매칭되지 않으면 `⚠ 확인 필요:` 표시를 붙여서 AI가 지어낸(환각)
> 법령명을 걸러낼 수 있게 합니다. 이 시크릿을 등록하지 않으면 이 검증 단계는
> 그냥 건너뛰고 기존처럼 AI가 생성한 법령명을 그대로 씁니다.

> **Gemini API 키 발급**: https://aistudio.google.com/apikey
> **모델 자동 폴백**: 무료 등급 모델 하나만 쓰면 하루 한도가 금방 차버리므로, 품질이
> 좋아지는 순서로 최대 3개 모델을 자동으로 돌아가며 씁니다.
> 1) `gemini-flash-lite-latest` (기본, 한도 넉넉함) → 2) `gemini-flash-latest` (1번 소진 시)
> → 3) `gemini-pro-latest` (2번도 소진 시, 최상위 품질). Google의 요청 한도(RPM/RPD)는
> 모델별로 완전히 별도로 집계되므로, 한 모델의 한도를 다 써도 다른 모델 사용에 불이익이
> 없고 오히려 하루 총 처리량이 늘어납니다. 모두 구글이 관리하는 `-latest` 별칭이라, 특정
> 버전(`gemini-2.5-flash` 등)이 단종돼도 자동으로 그 등급의 최신 모델을 가리키므로 모델
> 단종으로 인한 404를 예방할 수 있습니다. 다만 API 키/리전에 따라 사용 가능한 모델명이
> 다를 수 있는데, 사전 점검 단계에서 이 API 키로 못 쓰는 모델은 자동으로 목록에서 빼고
> 나머지로만 진행합니다(Actions 로그에서 확인 가능). 세 모델의 순서·RPM·일일 한도를
> 직접 조정하고 싶으면 저장소 **Variables**(Secrets 아님)에 아래 이름으로 등록하세요
> (모두 선택 사항, 등록 안 하면 기본값 사용):
> `GEMINI_MODEL_1_ID` / `_RPM` / `_RPD`, `GEMINI_MODEL_2_ID` / `_RPM` / `_RPD`,
> `GEMINI_MODEL_3_ID` / `_RPM` / `_RPD`.

### 5. 첫 실행
Actions 탭 → `Update Papers Pipeline` → `Run workflow`

> **분석 결과는 실행할 때마다 누적됩니다.** `collector.py`는 이미 수집된 논문(id 기준)은
> 건드리지 않고 새 논문만 추가하며, 이미 AI 분석이 끝난 논문(`ai_analysis`가 채워진 것)은
> `enricher.py`가 항상 건너뜁니다. 매 실행 끝에 `raw_papers.json` 등을 커밋·푸시해서
> 다음 실행이 그 상태를 이어받습니다. 다만 두 실행이 겹치면(예: 스케줄 실행 중 수동 실행)
> push 충돌로 한쪽 결과가 반영되지 않을 수 있어, 워크플로에 동시 실행 방지(concurrency)와
> push 실패 시 자동 재시도를 넣어뒀습니다. 그래도 분석 결과가 사라진 것 같으면 저장소의
> 커밋 히스토리에서 `chore: update papers` 커밋들이 실제로 쌓이고 있는지 확인해보세요.

> **API 요청 한도(모델별로 별도 집계, 자동 폴백)**
> `enricher.py`는 현재 사용 중인 모델의 RPM에 맞춰 요청 사이 간격을 자동으로 조절하고,
> 건당 출력 토큰도 제한해 TPM에 크게 못 미치도록 합니다. `enrich_state.json`에 오늘
> **모델별로** 사용한 요청 수를 따로 기록해, 각 모델의 자체 하루 한도(기본:
> flash-lite 100건 → flash 40건 → pro 15건, 총 155건)를 넘지 않도록 자동으로 다음
> 모델로 넘어가고, 세 모델을 모두 다 쓰면 그때 실행을 멈춥니다(날짜가 바뀌면 자동
> 초기화). 이 파일은 Actions가 커밋해서 저장소에 남기 때문에, 같은 날 여러 번 실행해도
> 누적된 사용량이 유지됩니다. 특정 모델에서 응답이 429(요청 한도 초과)로 오면 그 모델만
> 건너뛰고 바로 다음 모델로 전환해 같은 논문을 이어서 시도합니다. 하루 총량을 더
> 늘리고 싶으면 `Run workflow`의 `limit` 값을 올리거나, 위 `GEMINI_MODEL_n_RPD`
> Variables로 모델별 한도를 개별 조정하면 코드 수정 없이 반영됩니다.
> 이미 분석된 논문은 항상 건너뛰므로, 실행할 때마다 분석 결과가 이어서 누적됩니다.
> `limit`에 `1`을 넣으면 논문 1건만 테스트로 분석해볼 수 있습니다.

> **자동 실행 스케줄**: 매일 UTC 03:00(한국시간 낮 12시)에 자동으로 한 번 실행됩니다.
> Gemini의 일일 한도가 UTC 자정 기준으로 초기화되기 때문에, 그 이후 시간대로 잡아뒀습니다.
> Actions 탭에서 수동으로 `Run workflow`를 눌러 추가로 실행할 수도 있고, 자동 스케줄과
> 겹쳐도(동시 실행 방지 설정 덕분에) 안전합니다. 스케줄을 바꾸고 싶으면
> `.github/workflows/pipeline.yml`의 `cron` 값을 수정하세요.

---

## AI 분석 항목

각 논문에 다음 정보가 자동 생성됩니다:

| 항목 | 설명 |
|------|------|
| 초록 한글 번역 | 해외 논문 초록 전체를 자연스러운 한국어로 번역 |
| 3줄 핵심요약 | 배경·결과·시사점을 각 1줄로 요약 |
| 연구목적 | 2~3문장 실무 언어로 재서술 |
| 핵심결과 | 주요 수치·발견 3개 이상 |
| 실무 적용방안 | 현장 행동 중심 방안 3개 이상 |
| 국립공원 적용 가능성 | 1~5점 + 적용 근거 서술 (한국 국립공원 기준) |
| 관련 업무 분야 | 탐방로 관리, 생태계 모니터링 등 |
| 관련 법령 | 자연공원법 조항 등 |
| 현장점검 체크리스트 | 측정 가능한 체크 항목 5개 이상 |
| 실무 활용도 | 1~5점 |
| 적용 시 주의사항 | 예산·법령·계절 제약 등 |
| 관련 태그 | 검색·필터용 키워드 |
| 후속 연구 필요 내용 | 추가 연구 방향 제안 |
| AI 추천 연구 키워드 | 유사 논문 검색용 |

---

## 로컬 실행 (개발 시)

```bash
# 파이프라인 실행
export GEMINI_API_KEY="AIza..."
python run_pipeline.py

# 로컬 웹 서버
python -m http.server 8000
# → http://localhost:8000 에서 확인
```

## 라이선스
- 코드: MIT
- 논문 메타데이터: OpenAlex CC0
- AI 분석·번역 결과: 생성 주체 소유
