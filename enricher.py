#!/usr/bin/env python3
"""
Enricher
--------
Google Gemini API(기본 모델: gemini-flash-lite-latest)로 논문 초록을 분석해
1) 초록 한글 번역(abstract_ko)
2) 국립공원 실무 정보(ai_analysis)
를 함께 생성합니다.
역할: raw_papers.json 읽기 → AI 번역·분석 → raw_papers.json 업데이트

무료/제한 등급 기준(실측: RPM 15 / TPM 250,000 / RPD 500)에 맞춰
요청 간격·건당 토큰·일일 요청 수를 모두 제한합니다.
"""
import json, os, time, re
import requests

RAW_FILE   = "raw_papers.json"
STATE_FILE = "enrich_state.json"   # 일일 요청 수 누적 기록 (git에 커밋되어야 날짜가 바뀌기 전까지 유지됨)

GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest"
GEMINI_URL   = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# ── 요청 한도 (실측: RPM 15 / TPM 250,000 / RPD 500) ────────────────
# RPD는 실제 한도(500)보다 훨씬 낮게 잡아 여유를 둡니다. 필요하면 GEMINI_RPD_LIMIT/
# ENRICH_LIMIT 환경변수(또는 워크플로 limit 입력값)로 언제든 더 올릴 수 있습니다.
GEMINI_RPM_LIMIT = 15
GEMINI_RPD_LIMIT = int(os.getenv("GEMINI_RPD_LIMIT") or 100)
MAX_OUTPUT_TOKENS = 2000   # 건당 입력+출력 토큰을 넉넉히 잡아도 15건/분 기준 TPM 250k에 크게 못 미침
REQUEST_INTERVAL_SEC = (60 / GEMINI_RPM_LIMIT) + 1   # ≈ 5초, 분당 15건 이하로 유지

SYSTEM = """당신은 국립공원(한국 국립공원 포함) 관리 실무 전문가입니다.
해외 학술논문의 초록을 분석해 한국 국립공원 현장 실무자가 논문을 읽지 않아도
바로 업무에 적용할 수 있는 구체적인 정보를 JSON으로 제공합니다.
또한 초록 전체를 자연스러운 한국어로 번역합니다. 학술 언어를 실무 언어로 바꿔 서술하세요."""

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
    "1줄: 연구 배경과 목적",
    "2줄: 주요 방법과 결과",
    "3줄: 결론 및 실무 시사점"
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
  "related_work_areas": ["탐방로 관리", "생태계 모니터링"],
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
    """오늘 이미 사용한 요청 수를 읽어옵니다. 날짜가 바뀌었으면 0으로 초기화합니다."""
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    if state.get("date") != today_str():
        state = {"date": today_str(), "requests_today": 0}
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


def analyze(api_key: str, paper: dict) -> dict | str | None:
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

    try:
        r = requests.post(
            GEMINI_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if r.status_code == 429:
            print("  [Enricher] 429 요청 한도 초과 (RPM/TPM/RPD)")
            return "RATE_LIMIT"
        if r.status_code == 404:
            print(f"  [Enricher] 404 모델을 찾을 수 없음: {GEMINI_MODEL}")
            print(f"  [Enricher] 응답 내용: {r.text[:300]}")
            return "NOT_FOUND"
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = extract_json(text)
        result["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["model"]       = GEMINI_MODEL
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

    # ── 모델 사전 점검: 잘못된 모델명으로 요청을 반복해 일일 한도를 낭비하지 않도록 확인 ──
    available = list_generate_content_models(api_key)
    if available is not None and GEMINI_MODEL not in available:
        print(f"[Enricher] 설정된 모델 '{GEMINI_MODEL}'을(를) 이 API 키로 사용할 수 없습니다.")
        print(f"[Enricher] 사용 가능한 모델 목록: {', '.join(available[:15])}"
              + (" ..." if len(available) > 15 else ""))
        print("[Enricher] 저장소 Secrets에 GEMINI_MODEL을(를) 위 목록 중 하나로 등록한 뒤 "
              "다시 실행하세요. (예: gemini-flash-latest)")
        return

    # ── 일일 요청 한도(RPD) 확인 ──
    remaining_today = GEMINI_RPD_LIMIT - state["requests_today"]
    if remaining_today <= 0:
        print(f"[Enricher] 오늘({state['date']}) 일일 요청 한도({GEMINI_RPD_LIMIT}건)를 "
              f"이미 모두 사용했습니다. 내일(UTC 기준) 다시 시도하세요.")
        return

    # 한 번 실행당 분석 건수 = min(ENRICH_LIMIT, 오늘 남은 한도)
    requested_limit = int(os.getenv("ENRICH_LIMIT", str(GEMINI_RPD_LIMIT)))
    limit = min(requested_limit, remaining_today)

    with open(RAW_FILE, encoding="utf-8") as f:
        papers = json.load(f)

    pending = [p for p in papers
               if p.get("ai_analysis") is None and len(p.get("abstract", "")) > 100]
    print(f"[Enricher] 분석 대상: {len(pending)}건 / 전체 {len(papers)}건 "
          f"(오늘 남은 한도 {remaining_today}건, 이번 실행 최대 {limit}건)")

    done = 0
    fail_streak = 0
    for paper in papers:
        if done >= limit:
            print(f"[Enricher] 이번 실행 한도({limit}건) 도달 — 나머지는 다음 실행에서 처리")
            break
        if paper.get("ai_analysis") is not None:
            continue
        if len(paper.get("abstract", "")) < 100:
            continue

        preview = (paper.get("title") or "")[:50]
        print(f"  [{done+1}/{limit}] {preview}…")

        result = analyze(api_key, paper)

        if result == "NOT_FOUND":
            # 설정 오류(모델명 문제)이지 실제 요청 소비가 아니므로 일일 카운터에는 반영하지 않습니다.
            print("[Enricher] 모델 설정 오류로 이번 실행을 중단합니다. 일일 한도는 소비되지 않았습니다.")
            break

        # 성공이든 진짜 요청 실패든 요청 1건을 소비한 것으로 간주해 일일 카운터에 반영
        state["requests_today"] += 1
        save_daily_state(state)

        if result == "RATE_LIMIT":
            print("[Enricher] 요청 한도(RPM/TPM/RPD) 초과로 이번 실행을 중단합니다. "
                  "다음 실행 때 이어서 시도합니다.")
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

        if state["requests_today"] >= GEMINI_RPD_LIMIT:
            print(f"[Enricher] 오늘 일일 요청 한도({GEMINI_RPD_LIMIT}건) 도달 — 실행을 중단합니다.")
            break

        time.sleep(REQUEST_INTERVAL_SEC)   # RPM 한도 준수

    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    print(f"[Enricher] 완료: {done}건 분석됨 "
          f"(오늘 사용 {state['requests_today']}/{GEMINI_RPD_LIMIT}건, 대기 {len(pending)-done}건 남음)")


if __name__ == "__main__":
    run()
