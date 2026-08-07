#!/usr/bin/env python3
"""
Enricher
--------
Google Gemini API로 논문 초록을 분석해
1) 초록 한글 번역(abstract_ko)
2) 국립공원 실무 정보(ai_analysis)
를 함께 생성합니다.
역할: raw_papers.json 읽기 → AI 번역·분석 → raw_papers.json 업데이트

모델 자동 폴백: 무료 등급 모델 하나만 쓰면 하루 한도가 금방 차므로, 품질이 좋아지는
순서(flash-lite → flash → pro)로 여러 모델을 자동으로 돌아가며 씁니다. Google의 요청
한도(RPM/RPD)는 모델별로 완전히 별도 집계되므로, 한 모델의 한도를 다 쓰면 다음 모델로
넘어가는 방식이 안전하고(불이익 없음) 처리량도 늘어납니다. 모델명은 특정 버전을 못박지
않고 `-latest` 별칭(예: gemini-flash-lite-latest)을 써서, Google이 내부적으로 모델을
교체해도(예: 2.5 → 3.x) 코드 수정 없이 항상 그 등급의 최신 모델을 자동으로 씁니다.
"""
import json, os, time, re
import requests
import law_matcher

RAW_FILE   = "raw_papers.json"
STATE_FILE = "enrich_state.json"   # 일일 요청 수 누적 기록(모델별) — git에 커밋되어야 날짜가 바뀌기 전까지 유지됨

LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# ── 모델 자동 폴백 순서: 품질이 좋아지는 순서로(속도 우선이 아니라 결과 품질 우선) ──
# 각 모델의 rpd(하루 한도)는 무료 등급 실측치보다 여유 있게 낮춰 잡은 자체 안전 마진입니다.
# 실제 429가 뜨면 그 시점에 바로 다음 모델로 넘어가므로, 아래 숫자가 실제 한도와 다소
# 달라도 낭비되는 요청은 최소화됩니다. 필요하면 GEMINI_MODEL_1_RPD 등 환경변수로 개별 조정 가능.
def _model_cfg(idx: int, model_id: str, rpm: int, rpd: int) -> dict:
    return {
        "id":  os.getenv(f"GEMINI_MODEL_{idx}_ID")  or model_id,
        "rpm": int(os.getenv(f"GEMINI_MODEL_{idx}_RPM") or rpm),
        "rpd": int(os.getenv(f"GEMINI_MODEL_{idx}_RPD") or rpd),
    }

FALLBACK_MODELS = [
    _model_cfg(1, "gemini-flash-lite-latest", rpm=15, rpd=100),   # 1순위: 가장 가볍고 한도가 넉넉함
    _model_cfg(2, "gemini-flash-latest",      rpm=8,  rpd=40),    # 2순위: flash-lite 소진 시
    _model_cfg(3, "gemini-pro-latest",        rpm=4,  rpd=15),    # 3순위: 최상위 품질, 한도는 가장 적음
]

MAX_OUTPUT_TOKENS = 4096   # 스키마가 크고(제목·초록 번역 포함) 2000으로는 응답이 중간에 잘렸음.

SYSTEM = """당신은 국립공원(한국 국립공원 포함) 관리 실무 전문가입니다.
해외 학술논문의 초록을 분석해 한국 국립공원 현장 실무자가 논문을 읽지 않아도
바로 업무에 적용할 수 있는 구체적인 정보를 JSON으로 제공합니다.
또한 초록 전체를 자연스러운 한국어로 번역합니다. 학술 언어를 실무 언어로 바꿔 서술하세요."""

# 관련 업무 분야 고정 카테고리 (필터·통계용 — AI가 이 안에서만 고르도록 프롬프트에서 강제)
WORK_AREAS = [
    "생태계 모니터링·조사", "야생생물·서식지 관리", "자원보전·복원", "탐방로·탐방객 관리",
    "탐방객 서비스·안전", "시설·인프라 관리", "재난·안전 관리", "기후변화 대응",
    "환경 모니터링", "공원계획·구역관리", "지역사회·거버넌스", "관광·경제 정책",
]


def sanitize_work_areas(areas) -> list[str]:
    """AI가 12개 목록 밖의 표현을 반환하는 경우를 대비한 안전장치.
    목록에 없는 값은 버리고, 결과가 하나도 없으면 기본값 하나를 채웁니다."""
    if not isinstance(areas, list):
        return ["공원계획·구역관리"]
    valid = [a for a in areas if a in WORK_AREAS]
    if not valid:
        return ["공원계획·구역관리"]
    # 중복 제거, 최대 3개
    seen = []
    for a in valid:
        if a not in seen:
            seen.append(a)
    return seen[:3]


def sanitize_study_location(value) -> str:
    """AI가 '국내'/'해외' 외의 값을 반환하는 경우를 대비한 안전장치.
    수집 논문 대부분이 해외 사례이므로, 판단 불가 시 '해외'를 기본값으로 둡니다."""
    return value if value in ("국내", "해외") else "해외"

USER_TMPL = """다음 해외 국립공원 관련 논문을 분석하세요.

제목: {title}
저자: {authors}
학술지: {journal}  연도: {year}
초록(원문): {abstract}

반드시 아래 JSON 형식으로만 응답하세요 (```json 마크다운 없이, 다른 설명 없이 JSON 객체만):

{{
  "title_ko": "논문 제목을 자연스러운 한국어로 번역한 내용",
  "abstract_ko": "초록 전체를 자연스러운 한국어로 번역한 내용",
  "summary_3lines": [
    "연구 배경과 목적을 한 문장으로 (숫자·라벨 없이 바로 문장으로 시작)",
    "주요 방법과 결과를 한 문장으로",
    "결론 및 실무 시사점을 한 문장으로"
  ],
  "research_purpose": "연구 목적을 2~3문장으로 서술",
  "key_findings": ["핵심 결과 1", "핵심 결과 2", "핵심 결과 3"],
  "practical_applications": [
    "실무 적용방안 1 (구체적 행동 중심)",
    "실무 적용방안 2",
    "실무 적용방안 3"
  ],
  "korea_np_applicability_score": 4,
  "korea_np_applicability_reason": "한국 국립공원의 지형·생태·탐방 특성을 근거로 적용 가능한 이유 서술",
  "study_location": "해외",
  "related_work_areas": ["탐방로·탐방객 관리", "생태계 모니터링·조사"],
  "related_laws": ["자연공원법 제00조", "야생생물 보호 및 관리에 관한 법률 제00조"],
  "field_checklist": [
    "체크항목 1 (측정·확인 가능한 수준으로)",
    "체크항목 2",
    "체크항목 3",
    "체크항목 4",
    "체크항목 5"
  ],
  "practical_utility_score": 4,
  "cautions": ["주의사항 1 (예산·법령·계절 제약 등)", "주의사항 2"],
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "recommended_followup_research": ["후속 연구 필요 내용 1", "후속 연구 필요 내용 2"],
  "ai_recommended_topics": ["유사 연구 검색 키워드 1", "유사 연구 검색 키워드 2"]
}}

점수 기준
- korea_np_applicability_score: 1(무관)~5(직접 관련)
- practical_utility_score: 1(활용 어려움)~5(즉시 적용 가능)

study_location 판단 규칙
- 저자 소속기관이나 학술지 발행국과는 무관하게, 논문이 실제로 다루는 "연구 대상지"가
  어디인지로만 판단하세요.
- 연구 대상지(조사지·사례지·현장)가 대한민국의 국립공원·자연공원·보호지역이면 "국내",
  그 외 국가/지역이면 "해외"를 반환하세요.
- 특정 현장 없이 이론·리뷰·메타분석 위주이거나 여러 나라를 비교하며 한국이 주된
  대상이 아니면 "해외"로 반환하세요.
- 반드시 "국내" 또는 "해외" 둘 중 하나만 반환하세요.

related_work_areas 선택 규칙
- 반드시 아래 12개 목록 중에서만 1~3개를 골라 그대로(토씨 하나 안 틀리고) 반환하세요.
  새로운 표현을 만들어내지 마세요.
  ["생태계 모니터링·조사", "야생생물·서식지 관리", "자원보전·복원", "탐방로·탐방객 관리",
   "탐방객 서비스·안전", "시설·인프라 관리", "재난·안전 관리", "기후변화 대응",
   "환경 모니터링", "공원계획·구역관리", "지역사회·거버넌스", "관광·경제 정책"]

참고 법령: 자연공원법, 국립공원공단법, 문화재보호법, 야생생물 보호 및 관리에 관한 법률,
산림자원의 조성 및 관리에 관한 법률, 백두대간 보호에 관한 법률, 환경영향평가법"""


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    # 모델이 JSON 뒤에 여분의 텍스트(줄바꿈, 재출력 등)를 덧붙이는 경우가 있어
    # json.loads 대신 raw_decode로 첫 번째 유효한 JSON 객체만 잘라서 파싱합니다.
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text)
    return obj


def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def load_daily_state() -> dict:
    """오늘 이미 모델별로 사용한 요청 수를 읽어옵니다. 날짜가 바뀌었으면 0으로 초기화합니다."""
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    if state.get("date") != today_str() or not isinstance(state.get("requests_today"), dict):
        state = {"date": today_str(), "requests_today": {}}
    return state


def save_daily_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def list_generate_content_models(api_key: str) -> list[str] | None:
    """API 키로 실제 사용 가능한 모델 중 generateContent를 지원하는 모델 id 목록을 반환합니다.
    조회에 실패하면 None을 반환합니다."""
    try:
        r = requests.get(
            LIST_MODELS_URL,
            headers={"x-goog-api-key": api_key},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        ids = []
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                ids.append(m.get("name", "").split("/")[-1])
        return ids
    except Exception as exc:
        print(f"[Enricher] 모델 목록 조회 실패: {exc}")
        return None


def analyze(api_key: str, paper: dict, model_id: str) -> dict | str | None:
    """성공 시 dict, 요청 한도 초과(429) 시 'RATE_LIMIT', 모델을 찾을 수 없으면(404) 'NOT_FOUND',
    그 외 실패 시 None을 반환합니다."""
    abstract = (paper.get("abstract") or "").strip()
    if len(abstract) < 100:
        return None

    prompt = USER_TMPL.format(
        title    = paper.get("title", ""),
        authors  = ", ".join(paper.get("authors", [])[:3]) or "정보 없음",
        journal  = paper.get("journal", "정보 없음"),
        year     = paper.get("year", "정보 없음"),
        abstract = abstract[:3000],
    )

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
    try:
        r = requests.post(
            gemini_url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if r.status_code == 429:
            print(f"  [Enricher] 429 요청 한도 초과 (모델: {model_id})")
            return "RATE_LIMIT"
        if r.status_code == 404:
            print(f"  [Enricher] 404 모델을 찾을 수 없음: {model_id}")
            print(f"  [Enricher] 응답 내용: {r.text[:300]}")
            return "NOT_FOUND"
        r.raise_for_status()
        data = r.json()
        candidate = data["candidates"][0]
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            print(f"  [Enricher] 응답이 출력 토큰 한도({MAX_OUTPUT_TOKENS})에 걸려 중간에 잘렸습니다. "
                  f"MAX_OUTPUT_TOKENS를 더 늘려야 할 수 있습니다.")
        text = candidate["content"]["parts"][0]["text"]
        result = extract_json(text)
        result["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["model"]       = model_id
        result["related_work_areas"] = sanitize_work_areas(result.get("related_work_areas"))
        result["study_location"] = sanitize_study_location(result.get("study_location"))

        # LAW_API_OC가 설정되어 있으면 관련 법령을 실제 현행 법령과 대조합니다.
        law_oc = os.getenv("LAW_API_OC")
        if law_oc and result.get("related_laws"):
            result["related_laws"] = law_matcher.verify_related_laws(result["related_laws"], law_oc)

        return result
    except Exception as exc:
        print(f"  [Enricher] 실패: {exc}")
        return None


def run():
    # git add 대상 파일이 항상 존재하도록, API 키 유무와 무관하게 상태 파일을 먼저 기록합니다.
    state = load_daily_state()
    save_daily_state(state)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Enricher] GEMINI_API_KEY 없음 — 건너뜁니다.")
        return

    # ── 모델 사전 점검: 이 API 키로 실제 사용 가능한 모델만 폴백 목록에 남깁니다 ──
    available = list_generate_content_models(api_key)
    models = FALLBACK_MODELS
    if available is not None:
        models = [m for m in FALLBACK_MODELS if m["id"] in available]
        skipped = [m["id"] for m in FALLBACK_MODELS if m["id"] not in available]
        if skipped:
            print(f"[Enricher] 이 API 키로 사용 불가한 모델(건너뜀): {', '.join(skipped)}")
    if not models:
        print(f"[Enricher] 사용 가능한 모델이 하나도 없습니다. 사용 가능 목록: "
              f"{', '.join((available or [])[:15])}")
        print("[Enricher] 저장소 Secrets/Variables의 GEMINI_MODEL_1_ID 등으로 직접 지정해보세요.")
        return

    print("[Enricher] 오늘 모델별 사용량: " +
          ", ".join(f"{m['id']}={state['requests_today'].get(m['id'], 0)}/{m['rpd']}" for m in models))

    with open(RAW_FILE, encoding="utf-8") as f:
        papers = json.load(f)

    pending = [p for p in papers
               if p.get("ai_analysis") is None and len(p.get("abstract", "")) > 100]
    # 최신 논문부터 분석 — year가 없는 경우(이론상 없어야 하지만 방어적으로) 가장 뒤로
    pending.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"[Enricher] 분석 대상: {len(pending)}건 / 전체 {len(papers)}건 (최신 발행연도부터 분석)")

    requested_limit = int(os.getenv("ENRICH_LIMIT") or sum(m["rpd"] for m in models))

    done = 0
    fail_streak = 0
    model_idx = 0   # 한 번 다음 모델로 넘어가면 이번 실행 동안은 되돌아가지 않음(품질 우선 순서 유지)

    for paper in pending:
        if done >= requested_limit:
            print(f"[Enricher] 이번 실행 한도({requested_limit}건) 도달 — 나머지는 다음 실행에서 처리")
            break

        # ── 현재 논문 하나를, 오늘 한도가 남은 모델을 찾아가며 시도 ──
        result = None
        used_model = None
        while model_idx < len(models):
            model = models[model_idx]
            used_today = state["requests_today"].get(model["id"], 0)
            if used_today >= model["rpd"]:
                print(f"[Enricher] {model['id']} 오늘 한도({model['rpd']}건) 소진 — 다음 모델로 전환")
                model_idx += 1
                continue

            preview = (paper.get("title") or "")[:50]
            print(f"  [{done+1}] ({model['id']}) {preview}…")
            result = analyze(api_key, paper, model["id"])

            if result == "NOT_FOUND":
                print(f"[Enricher] {model['id']} 이 API 키로 사용 불가 — 다음 모델로 전환 (한도 소비 없음)")
                model_idx += 1
                continue

            # 성공이든 진짜 요청 실패든 요청 1건을 소비한 것으로 간주해 모델별 카운터에 반영
            state["requests_today"][model["id"]] = used_today + 1
            save_daily_state(state)

            if result == "RATE_LIMIT":
                print(f"[Enricher] {model['id']} 요청 한도 초과 — 다음 모델로 전환해 이 논문을 이어서 시도합니다.")
                model_idx += 1
                continue

            used_model = model["id"]
            break   # 성공 또는 진짜 실패(None) — 이 논문 처리는 끝, 다음 논문으로

        if model_idx >= len(models):
            print("[Enricher] 오늘 사용 가능한 모든 모델의 한도를 소진했습니다 — 실행을 중단합니다.")
            break

        if result:
            paper["ai_analysis"] = result
            done += 1
            fail_streak = 0
            # 건별로 즉시 저장 — 한도 초과·오류로 중단되어도 그때까지의 결과는 보존됩니다.
            with open(RAW_FILE, "w", encoding="utf-8") as f:
                json.dump(papers, f, ensure_ascii=False, indent=2)
        else:
            fail_streak += 1
            if fail_streak >= 3:
                print("[Enricher] 연속 3건 실패 — 이번 실행을 중단합니다. "
                      "다음 실행 때 이어서 시도합니다.")
                break

        time.sleep((60 / models[model_idx]["rpm"]) + 1)   # 현재 사용 중인 모델의 RPM 한도 준수

    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    usage_str = ", ".join(f"{m['id']}={state['requests_today'].get(m['id'], 0)}/{m['rpd']}" for m in models)
    print(f"[Enricher] 완료: {done}건 분석됨 (오늘 사용 {usage_str}, 대기 {len(pending)-done}건 남음)")


if __name__ == "__main__":
    run()
