#!/usr/bin/env python3
"""
Law Matcher
-----------
국가법령정보 공동활용 Open API(law.go.kr)로 AI가 제시한 "관련 법령" 목록을
실제 존재하는 현행 법령과 대조해 정확도를 높입니다.

사용하려면:
1. https://open.law.go.kr 에서 회원가입 후 "OPEN API 활용신청"을 합니다.
2. 신청 시 사용한 아이디(OC)를 LAW_API_OC 환경변수/시크릿으로 등록합니다.
LAW_API_OC가 설정되어 있지 않으면 이 모듈은 아무 동작도 하지 않고
AI가 생성한 법령명을 그대로 둡니다(기존 동작과 동일).

주의: law.go.kr의 JSON 응답 필드명은 공식 문서 기준으로 작성했지만,
실제 응답에서 필드명이 다르게 오는 경우 아래 name_of()/law.get(...) 부분을
Actions 로그에 찍히는 원본 응답을 보고 맞춰야 할 수 있습니다.
"""
import re
import time
import requests

LAW_SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"

# "자연공원법 제30조", "자연공원법 제3조제1항" 등에서 조항 표기를 떼어내기 위한 패턴
_ARTICLE_SUFFIX = re.compile(r"\s*제\s*\d+.*$")


def _extract_law_name(raw: str) -> str:
    """'자연공원법 제30조' → '자연공원법'처럼 법령명만 추출합니다."""
    return _ARTICLE_SUFFIX.sub("", raw).strip()


def search_law(law_name: str, oc: str) -> dict | None:
    """법령명으로 국가법령정보 API를 검색해 가장 근접한 현행 법령 하나를 반환합니다.
    매칭 실패/오류 시 None을 반환합니다."""
    if not law_name:
        return None
    try:
        r = requests.get(
            LAW_SEARCH_URL,
            params={
                "OC": oc,
                "target": "law",
                "type": "JSON",
                "query": law_name,
                "display": 5,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        laws = (data.get("LawSearch") or {}).get("law", [])
        if isinstance(laws, dict):
            laws = [laws]
        if not laws:
            return None

        def name_of(l):
            return l.get("법령명한글") or l.get("법령명") or ""

        # 이름이 완전히 일치하는 현행 법령을 우선 사용, 없으면 검색 결과 1순위 사용
        exact = [l for l in laws if name_of(l) == law_name]
        law = exact[0] if exact else laws[0]

        link_path = law.get("법령상세링크") or ""
        link = f"https://www.law.go.kr{link_path}" if link_path.startswith("/") else (link_path or None)

        return {
            "name":   name_of(law),
            "law_id": law.get("법령ID") or law.get("법령일련번호"),
            "link":   link,
        }
    except Exception as exc:
        print(f"  [LawMatcher] 조회 실패 ({law_name}): {exc}")
        return None


def verify_related_laws(related_laws: list, oc: str) -> list:
    """AI가 생성한 관련 법령 목록을 실제 법령 DB와 대조해 정리합니다.
    - 매칭되면: 실제(현행) 법령명 + 원래 조항 표기를 붙여서 반환
    - 매칭 안 되면: '⚠ 확인 필요: ...' 표시를 붙여 걸러낼 수 있게 함
    """
    if not oc or not related_laws:
        return related_laws

    verified = []
    for raw in related_laws:
        name = _extract_law_name(raw)
        if not name:
            verified.append(raw)
            continue

        result = search_law(name, oc)
        time.sleep(0.2)   # API에 과도한 연속 요청을 피하기 위한 최소 간격

        if result and result.get("name"):
            article = raw[len(name):].strip() if raw.startswith(name) else ""
            verified.append(f"{result['name']} {article}".strip())
        else:
            verified.append(f"⚠ 확인 필요: {raw}")

    return verified
