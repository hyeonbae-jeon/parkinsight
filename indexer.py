#!/usr/bin/env python3
"""
Indexer
-------
raw_papers.json → papers.json 변환
검색·필터·통계에 필요한 인덱스를 생성합니다.
"""
import json, os
from collections import Counter
from datetime import datetime

RAW_FILE    = "raw_papers.json"
OUTPUT_FILE = "papers.json"


def sort_key(p: dict):
    ai = p.get("ai_analysis") or {}
    return (
        -(ai.get("korea_np_applicability_score") or 0),
        -(ai.get("practical_utility_score") or 0),
        -(p.get("cited_by_count") or 0),
    )


def build_stats(papers: list) -> dict:
    analyzed = [p for p in papers if p.get("ai_analysis")]
    app = [p["ai_analysis"]["korea_np_applicability_score"]
           for p in analyzed
           if isinstance((p["ai_analysis"] or {}).get("korea_np_applicability_score"), int)]
    util = [p["ai_analysis"]["practical_utility_score"]
            for p in analyzed
            if isinstance((p["ai_analysis"] or {}).get("practical_utility_score"), int)]
    year_dist = dict(sorted(Counter(p["year"] for p in papers if p.get("year")).items()))

    return {
        "total":                    len(papers),
        "analyzed":                 len(analyzed),
        "avg_applicability":        round(sum(app)  / len(app),  1) if app  else 0,
        "avg_utility":              round(sum(util) / len(util), 1) if util else 0,
        "high_applicability_count": sum(1 for s in app if s >= 4),
        "score_distribution":       {str(s): app.count(s) for s in range(1, 6)},
        "year_distribution":        year_dist,
        "updated_at":               datetime.now().isoformat(),
    }


def build_tag_index(papers: list) -> dict:
    c = Counter()
    for p in papers:
        for tag in (p.get("ai_analysis") or {}).get("tags", []):
            c[tag] += 1
    return dict(c.most_common(60))


def build_work_area_index(papers: list) -> dict:
    c = Counter()
    for p in papers:
        for area in (p.get("ai_analysis") or {}).get("related_work_areas", []):
            c[area] += 1
    return dict(c.most_common(30))


def run():
    if not os.path.exists(RAW_FILE):
        print(f"[Indexer] {RAW_FILE} 없음")
        return

    with open(RAW_FILE, encoding="utf-8") as f:
        papers = json.load(f)

    papers.sort(key=sort_key)

    output = {
        "meta":             build_stats(papers),
        "tag_index":        build_tag_index(papers),
        "work_area_index":  build_work_area_index(papers),
        "papers":           papers,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    m = output["meta"]
    print(f"[Indexer] 완료: {m['total']}건 중 {m['analyzed']}건 분석됨 → {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
