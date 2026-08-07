#!/usr/bin/env python3
"""
reclassify_study_location.py (1회성 마이그레이션 스크립트)
------------------------------------------------------
이미 AI 분석이 끝난 논문들에 새로 추가된 ai_analysis.study_location("국내"/"해외")
필드를 소급으로 채웁니다.

- Gemini를 다시 호출하지 않고, 제목·초록 원문(raw_papers.json의 title/abstract, 영문)에
  한국 국립공원 이름이나 "Korea" 계열 표현이 등장하는지로 판단하는 키워드 규칙을 씁니다.
  (EXTRA_KEYWORDS로 등록했던 24개 한국 국립공원명 그대로 사용)
- 정확도는 향후 AI가 매 논문 내용을 읽고 판단하는 것보다 낮을 수 있습니다. 그래서
  이 스크립트로 채운 값에는 study_location_method="keyword"를 함께 표시해두어,
  나중에 필요하면 이 값들만 골라 AI로 재검증할 수 있게 해뒀습니다.
- ai_analysis의 다른 필드는 전혀 건드리지 않습니다.

사용법: python3 reclassify_study_location.py
"""
import json, re

RAW_FILE = "raw_papers.json"

KOREA_PARK_NAMES = [
    "Gayasan", "Geumjeongsan", "Gyeongju", "Gyeryongsan", "Naejangsan",
    "Dadohaehaesang", "Deogyusan", "Mudeungsan", "Byeonsanbando", "Bukhansan",
    "Seoraksan", "Sobaeksan", "Songnisan", "Odaesan", "Woraksan", "Wolchulsan",
    "Juwangsan", "Jirisan", "Chiaksan", "Taebaeksan", "Taeanhaean", "Palgongsan",
    "Hallyeohaesang", "Hallasan",
]
KOREA_GENERIC = [
    "south korea", "republic of korea", "korean national park",
    "korea national park", "korea peninsula", "korean peninsula",
]

_PATTERNS = [re.compile(r"\b" + re.escape(kw.lower()) + r"\b") for kw in KOREA_PARK_NAMES + KOREA_GENERIC]


def classify(title: str, abstract: str) -> str:
    text = f"{title or ''} {abstract or ''}".lower()
    for pat in _PATTERNS:
        if pat.search(text):
            return "국내"
    return "해외"


def run():
    with open(RAW_FILE, encoding="utf-8") as f:
        papers = json.load(f)

    changed = 0
    domestic = 0
    for p in papers:
        ai = p.get("ai_analysis")
        if not ai:
            continue
        if "study_location" in ai:
            continue   # 이미 값이 있으면 건드리지 않음 (향후 AI가 채운 것 포함)

        loc = classify(p.get("title", ""), p.get("abstract", ""))
        ai["study_location"] = loc
        ai["study_location_method"] = "keyword"
        changed += 1
        if loc == "국내":
            domestic += 1

    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)

    print(f"[Reclassify] 총 {len(papers)}건 중 {changed}건에 study_location을 채웠습니다.")
    print(f"[Reclassify] 그중 '국내'로 분류된 논문: {domestic}건, '해외': {changed - domestic}건")
    print("[Reclassify] 다음으로 'python3 indexer.py'를 실행해서 papers.json을 갱신하세요.")


if __name__ == "__main__":
    run()
