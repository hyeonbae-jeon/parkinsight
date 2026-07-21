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

## 배포 방법

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
| `GEMINI_MODEL` | 사용할 모델 id (선택 — 기본값 `gemini-flash-lite-latest`) |
| `OPENALEX_EMAIL` | 이메일 주소 (선택 — API 요청 속도 향상) |

> **Gemini API 키 발급**: https://aistudio.google.com/apikey
> **모델**: 기본값은 `gemini-flash-lite-latest`입니다. 이건 구글이 관리하는 "별칭"으로,
> 특정 버전(`gemini-2.5-flash-lite` 등)이 신규 사용자에게 더 이상 제공되지 않게 되어도
> 자동으로 현재 사용 가능한 최신 Flash-Lite 모델을 가리키므로 모델 단종으로 인한 404를
> 예방할 수 있습니다. 그래도 API 키/리전에 따라 사용 가능한
> 모델명이 다를 수 있습니다. Actions 로그에 `404 모델을 찾을 수 없음`이 뜨면, 같은 로그에
> 그 키로 실제 사용 가능한 모델 목록이 함께 출력됩니다(모델 사전 점검 단계에서 자동 조회).
> 그 목록 중 하나를 골라 `GEMINI_MODEL` 시크릿으로 등록하면 코드 수정 없이 바로 반영됩니다.
> 이 사전 점검 덕분에 모델명이 잘못돼도 일일 요청 한도(RPD)는 소비되지 않습니다.

### 5. 첫 실행
Actions 탭 → `Update Papers Pipeline` → `Run workflow`

> **분석 결과는 실행할 때마다 누적됩니다.** `collector.py`는 이미 수집된 논문(id 기준)은
> 건드리지 않고 새 논문만 추가하며, 이미 AI 분석이 끝난 논문(`ai_analysis`가 채워진 것)은
> `enricher.py`가 항상 건너뜁니다. 매 실행 끝에 `raw_papers.json` 등을 커밋·푸시해서
> 다음 실행이 그 상태를 이어받습니다. 다만 두 실행이 겹치면(예: 스케줄 실행 중 수동 실행)
> push 충돌로 한쪽 결과가 반영되지 않을 수 있어, 워크플로에 동시 실행 방지(concurrency)와
> push 실패 시 자동 재시도를 넣어뒀습니다. 그래도 분석 결과가 사라진 것 같으면 저장소의
> 커밋 히스토리에서 `chore: update papers` 커밋들이 실제로 쌓이고 있는지 확인해보세요.

> **API 요청 한도(실측: RPM 15 / TPM 250,000 / RPD 500)에 맞춘 기본 설정**
> `enricher.py`는 요청 사이 간격을 약 5초(≈60초/15회)로 두어 분당 요청 수가 15건을
> 넘지 않도록 하고, 건당 출력 토큰도 제한해 분당 토큰 수가 25만 토큰에 크게 못 미치도록
> 합니다. 그리고 `enrich_state.json`에 오늘 사용한 요청 수를 기록해, 하루 총 요청이
> 기본 100건(실제 한도 500건보다 넉넉히 낮게 설정)을 넘지 않도록 자동으로 멈춥니다
> (날짜가 바뀌면 자동 초기화). 이 파일은 Actions가 커밋해서 저장소에 남기 때문에, 같은
> 날 여러 번 실행해도 누적된 사용량이 유지됩니다. 응답이 429(요청 한도 초과)로 오면
> 그 즉시 실행을 중단해 한도를 낭비하지 않습니다. 하루 100건보다 더 처리하고 싶으면
> `Run workflow`의 `limit` 값을 올리거나, `GEMINI_RPD_LIMIT` 시크릿에 원하는 값(최대
> 500 미만 권장)을 등록하면 코드 수정 없이 반영됩니다.
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
