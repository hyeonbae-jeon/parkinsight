#!/usr/bin/env python3
"""
Pipeline Runner
---------------
Collector → Enricher → Indexer 순서로 실행합니다.
"""
import collector, enricher, indexer

def main():
    print("=" * 55)
    print("국립공원 실무 AI 지식 플랫폼 — 파이프라인 시작")
    print("=" * 55)
    print("\n[1/3] Collector: 논문 수집 중…")
    collector.run()
    print("\n[2/3] Enricher: AI 분석 중…")
    enricher.run()
    print("\n[3/3] Indexer: 인덱스 생성 중…")
    indexer.run()
    print("\n✓ 파이프라인 완료")

if __name__ == "__main__":
    main()
