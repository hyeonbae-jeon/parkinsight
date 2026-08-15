#!/usr/bin/env python3
"""
Law Matcher
-----------
국가법령정보 공동활용 Open API(law.go.kr)로 AI가 제시한 "관련 법령" 목록을
실제 존재하는 현행 법령과 대조해 정확도를 높입니다.

사용하려면:
1. https://open.law.go.kr 에서 회원가입 후 "OPEN API 활용신청"을 합니다.
   (별도의 API key는 없습니다 — 가입할 때 쓴 아이디 자체가 인증값(OC)입니다.
   예: 가입 아이디가 "hyeonbae123"이면 OC=hyeonbae123)
2. 그 아이디를 LAW_API_OC 환경변수/시크릿으로 등록합니다.
LAW_API_OC가 설정되어 있지 않으면 이 모듈은 아무 동작도 하지 않고
AI가 생성한 법령명을 그대로 둡니다(기존 동작과 동일).

주의(XML을 쓰는 이유): law.go.kr는 문서상 type=JSON을 지원한다고 되어 있지만,
실제로는 JSON 요청이 안정적으로 동작하지 않는 사례가 다수 보고되어 있습니다.
그래서 이 모듈은 공식 문서·실제 응답 사례 모두에서 안정적으로 확인되는
XML 응답을 파싱합니다.

주의(클라우드 IP 차단 가능성): law.go.kr는 AWS/GCP/Azure 등 클라우드 호스팅 IP
대역에서 오는 요청을 봇으로 간주해 차단하는 사례가 보고되어 있습니다. GitHub
Actions의 실행 서버도 Azure 클라우드 IP를 쓰기 때문에, Actions에서 이 모듈을
호출하면 연결이 타임아웃되며 실패할 수 있습니다(코드 문제가 아님). 아래
브라우저 User-Agent 위장으로 일부 케이스는 우회되지만, 방화벽 단계에서 IP
자체를 막는 경우엔 이것으로도 해결되지 않을 수 있습니다 — 그럴 땐 Actions가
아닌 일반 네트워크(개인 PC 등)에서 실행해야 합니다.
"""
import re
import time
import requests
import xml.etree.ElementTree as ET

LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"

# 클라우드 호스팅 IP에서의 봇 차단을 일부라도 피해보기 위한 일반 브라우저 UA 위장
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}

# "자연공원법 제30조", "자연공원법 제3조제1항" 등에서 조항 표기를 떼어내기 위한 패턴
_ARTICLE_SUFFIX = re.compile(r"\s*제\s*\d+.*$")


def _extract_law_name(raw: str) -> str:
    """'자연공원법 제30조' → '자연공원법'처럼 법령명만 추출합니다."""
    return _ARTICLE_SUFFIX.sub("", raw).strip()


def _text(el, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def search_law(law_name: str, oc: str) -> dict | None:
    """법령명으로 국가법령정보 API를 검색해 가장 근접한 현행 법령 하나를 반환합니다.
    매칭 실패/오류 시 None을 반환합니다."""
    if not law_name:
        return None
    r = None
    try:
        r = requests.get(
            LAW_SEARCH_URL,
            params={
                "OC": oc,
                "target": "law",
                "type": "XML",
                "query": law_name,
                "display": 5,
            },
            headers=_HEADERS,
            timeout=5,
        )
        r.raise_for_status()
        # law.go.kr 응답은 자체 XML 선언에 인코딩이 명시되어 있으므로 r.content(바이트)를
        # 그대로 넘겨 ElementTree가 인코딩을 스스로 판단하게 합니다.
        root = ET.fromstring(r.content)

        laws = root.findall("law")
        if not laws:
            return None

        def name_of(el):
            return _text(el, "법령명한글")

        # 이름이 완전히 일치하는 현행 법령을 우선 사용, 없으면 검색 결과 1순위 사용
        exact = [l for l in laws if name_of(l) == law_name]
        law = exact[0] if exact else laws[0]

        link_path = _text(law, "법령상세링크")
        link = f"https://www.law.go.kr{link_path}" if link_path.startswith("/") else (link_path or None)

        return {
            "name":   name_of(law),
            "law_id": _text(law, "법령ID") or _text(law, "법령일련번호"),
            "link":   link,
        }
    except ET.ParseError as exc:
        preview = r.text[:200] if r is not None else ""
        print(f"  [LawMatcher] XML 파싱 실패 ({law_name}): {exc} — 응답 미리보기: {preview}")
        return None
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
