#!/usr/bin/env python3
"""
Collector
---------
OpenAlex REST API에서 해외 국립공원 관리·연구 관련 논문을 수집합니다.
역할: 검색 → 정규화 → raw_papers.json에 누적 저장

체크포인트(progress.json): 검색어 단위로 "완료 일자"와 "실패 횟수"를 기록합니다.
- 검색어 텍스트 자체를 키로 사용하므로(=index 기반 아님), QUERIES 순서를 바꾸거나
  새 검색어를 추가해도 이미 완료한 검색어는 건드리지 않고 새 것만 처리합니다.
- 한 검색어를 끝까지(결과 소진 또는 limit 도달) 가져오지 못하고 429/시간초과로
  중단되면 그 검색어는 "미완료"로 남고 실패 횟수가 1 증가합니다. 이때 전체 실행을
  멈추지 않고 바로 다음 검색어로 넘어갑니다 — 한 단어가 계속 막혀도 그 뒤에 있는
  아직 시도 안 한 새 단어들이 영영 처리되지 못하는 일이 없도록 하기 위함입니다.
- 완료된 검색어도 REFRESH_INTERVAL_DAYS(기본 14일)가 지나면 자동으로 다시 검색
  대상에 포함됩니다. OpenAlex는 계속 새 논문이 추가되는 살아있는 DB라서, 한 번
  다 모았다고 영원히 손 놓으면 그 뒤에 나온 신규 논문을 놓치기 때문입니다. 이미
  가지고 있는 논문은 id 기준으로 자동 스킵되므로 중복 없이 새로 늘어난 것만
  추가됩니다.
- 매 실행마다 (1) 한 번도 안 해본 새 검색어 → (2) 재검색 주기가 된 검색어 순으로
  처리하고, 각 그룹 안에서는 "실패 횟수가 적은 순"으로 정렬합니다. 즉 새 검색어가
  항상 최우선이고, 반복해서 실패해온 검색어는 그 그룹 안에서 계속 뒤로 밀려납니다.
- 429 등으로 중단되어도 프로그램은 정상 종료(exit code 0)해서 Actions가
  실패로 표시되지 않게 하고, 그때까지 모은 데이터는 그대로 유지합니다.
"""
import requests, json, os, time
from datetime import datetime

OPENALEX = "https://api.openalex.org"
RAW_FILE      = "raw_papers.json"
STATE_FILE    = "fetch_state.json"
PROGRESS_FILE = "progress.json"   # 검색어 단위 완료 체크포인트

# 국립공원 실무(탐방로 관리, 생태계 모니터링, 방문객 관리 등) 관련 검색어
# "national park"이 모든 검색어에 들어가도록 해서, 결과가 국립공원과 무관한
# 논문(예: 의학·일반 생태학 논문)으로 새는 것을 최대한 줄입니다.
QUERIES = [
    "national park trail management",
    "national park visitor management",
    "national park ecosystem monitoring",
    "national park biodiversity conservation",
    "national park trail erosion",
    "national park wildlife management",
    "national park management effectiveness",
    "national park climate change adaptation",
    "national park carrying capacity",
    "national park invasive species management",
    "national park fire management",
    "national park recreation ecology",
    "national park governance",
    "national park restoration ecology",
    "national park human wildlife conflict",
    "national park visitor experience",
    "national park ranger management",
    "national park tourism impact",
    "national park boundary encroachment",
    "national park zoning planning",
    "national park cultural heritage management",
    "national park entrance fee policy",
    "national park signage interpretation",
    "national park camping impact",
    "national park air quality monitoring",
    "national park water quality monitoring",
    "national park soil erosion control",
    "national park economic valuation",
    "national park community engagement",
    "national park drone remote sensing",
    "national park citizen science monitoring",
    "national park wetland management",
    "national park landscape connectivity corridor",
    "national park disaster risk management",
    "national park accessibility disability",
    "national park volunteer program",
    "national park noise light pollution",
]


REFRESH_INTERVAL_DAYS = int(os.getenv("COLLECTOR_REFRESH_DAYS") or 14)
# 완료된 검색어도 이 일수가 지나면 다시 검색 대상에 포함시킵니다.
# OpenAlex는 계속 새 논문이 추가되는 살아있는 DB라서, 한 번 다 모았다고 영원히
# 손 놓으면 그 뒤로 나온 신규 논문을 놓치게 됩니다. (완전 재수집이 아니라 페이지를
# 다시 훑는 것뿐이라 이미 있는 논문은 그대로 스킵되고, 새로 늘어난 것만 추가됩니다.)


def load_progress() -> tuple[dict, dict]:
    """완료된 검색어 → 완료 일자(YYYY-MM-DD) 매핑과, 검색어별 실패 횟수를 불러옵니다.
    옛 형식(completed_keywords가 리스트)이면 오늘 날짜로 완료된 것으로 간주해 변환합니다."""
    if not os.path.exists(PROGRESS_FILE):
        return {}, {}
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("completed_keywords", {})
        if isinstance(raw, list):
            # 옛 형식 마이그레이션: 완료 시점을 알 수 없으니 오늘 날짜로 기록해
            # 갑자기 61개가 한꺼번에 재검색 대상이 되는 것을 방지합니다.
            today = today_str()
            completed = {q: today for q in raw}
        else:
            completed = dict(raw)
        return completed, dict(data.get("fail_counts", {}))
    except Exception as exc:
        print(f"[Collector] progress.json 읽기 실패({exc}) — 처음부터 다시 진행합니다.")
        return {}, {}


def save_progress(completed: dict, fail_counts: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed_keywords": completed,
            "fail_counts": fail_counts,
            "updated_at": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def days_since(date_str: str) -> float:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return REFRESH_INTERVAL_DAYS + 1   # 날짜 파싱 실패 시 즉시 재검색 대상으로 취급
    return (datetime.now() - d).total_seconds() / 86400


def load_extra_keywords() -> list:
    """EXTRA_KEYWORDS 환경변수(쉼표 또는 줄바꿈으로 구분)에서 추가 검색어를 읽습니다.
    Actions의 workflow_dispatch 입력값이나 저장소 Variable로 코드 수정 없이 검색어를 추가할 수 있습니다."""
    raw = os.getenv("EXTRA_KEYWORDS", "")
    if not raw.strip():
        return []
    parts = [p.strip() for chunk in raw.split("\n") for p in chunk.split(",")]
    return [p for p in parts if p]


def save_raw(existing: dict) -> None:
    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)


def reconstruct_abstract(inv: dict) -> str:
    """OpenAlex abstract_inverted_index → 일반 텍스트 변환"""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def normalize(raw: dict) -> dict:
    loc = raw.get("primary_location") or {}
    src = loc.get("source") or {}
    oa  = raw.get("open_access") or {}
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in raw.get("authorships", [])
    ]
    concepts = [c.get("display_name", "") for c in raw.get("concepts", [])[:6]]
    return {
        "id":             raw.get("id", "").split("/")[-1],
        "source":         "openalex",
        "title":          raw.get("title") or "",
        "abstract":       reconstruct_abstract(raw.get("abstract_inverted_index") or {}),
        "authors":        [a for a in authors if a],
        "year":           raw.get("publication_year"),
        "journal":        src.get("display_name", ""),
        "cited_by_count": raw.get("cited_by_count", 0),
        "doi":            raw.get("doi") or "",
        "is_oa":          oa.get("is_oa", False),
        "oa_url":         oa.get("oa_url") or "",
        "concepts":       concepts,
        "openalex_url":   raw.get("id", ""),
        "ai_analysis":    None,
    }


def fetch_query(query: str, email: str = "", limit: int = 100, deadline: float | None = None):
    """반환값: (papers, completed).
    completed=True  → 결과를 소진했거나 limit에 도달해 이 검색어를 끝까지 처리함
    completed=False → 429/오류/시간초과로 중간에 중단됨 (다음 실행에서 이 검색어를 처음부터 재시도)"""
    papers, cursor = [], "*"
    select = (
        "id,title,abstract_inverted_index,authorships,"
        "publication_year,primary_location,cited_by_count,"
        "concepts,open_access,doi"
    )
    while len(papers) < limit:
        if deadline is not None and time.time() > deadline:
            print(f"[Collector] 실행 시간 한도 도달 — '{query[:30]}' 검색을 중단합니다.")
            return papers, False
        batch  = min(25, limit - len(papers))
        params = {
            "filter":   f"title_and_abstract.search:{query},has_abstract:true",
            "per-page": batch,
            "cursor":   cursor,
            "select":   select,
        }
        if email:
            params["mailto"] = email

        r = None
        MAX_ATTEMPTS = 8
        WAIT_CAP = 30
        for attempt in range(MAX_ATTEMPTS):
            if deadline is not None and time.time() > deadline:
                print(f"[Collector] 실행 시간 한도 도달 — '{query[:30]}' 재시도를 중단합니다.")
                r = None
                break
            try:
                r = requests.get(f"{OPENALEX}/works", params=params, timeout=30)
                if r.status_code == 429:
                    # Retry-After 값이 비정상적으로 크게 와도 최대 20초까지만 대기
                    raw_wait = int(r.headers.get("Retry-After", 0)) or (2 ** attempt) * 3
                    wait = min(raw_wait, WAIT_CAP)
                    if deadline is not None:
                        wait = min(wait, max(0, deadline - time.time()))
                    print(f"[Collector] 429 (요청 과다) — {wait:.0f}초 대기 후 재시도 "
                          f"({attempt+1}/{MAX_ATTEMPTS}) [{query[:30]}]")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                break
            except requests.exceptions.RequestException as exc:
                wait = min(2 ** attempt, WAIT_CAP)
                if deadline is not None:
                    wait = min(wait, max(0, deadline - time.time()))
                print(f"[Collector] 오류 ({query[:30]}): {exc} — {wait:.0f}초 후 재시도")
                time.sleep(wait)
                r = None
        if r is None or r.status_code != 200:
            print(f"[Collector] 포기 ({query[:30]}): 재시도 {MAX_ATTEMPTS}회 모두 실패 — 이 검색어는 다음 실행에서 재시도")
            return papers, False

        try:
            data    = r.json()
            results = data.get("results", [])
            if not results:
                return papers, True   # 결과 소진 → 완료
            for item in results:
                n = normalize(item)
                if n["title"]:
                    papers.append(n)
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                return papers, True   # 다음 페이지 없음 → 완료
            time.sleep(0.5)   # 요청 사이 간격을 조금 더 넉넉하게
        except Exception as exc:
            print(f"[Collector] 파싱 오류 ({query[:30]}): {exc} — 이 검색어는 다음 실행에서 재시도")
            return papers, False

    return papers, True   # limit 도달 → 완료


def run():
    email = os.getenv("OPENALEX_EMAIL", "")
    if not email:
        print("[Collector] 경고: OPENALEX_EMAIL이 설정되지 않았습니다. "
              "이메일 없이는 OpenAlex 속도 제한이 훨씬 낮아 429가 자주 발생하고 "
              "실행 시간이 크게 늘어날 수 있습니다. 저장소 Secrets에 등록을 권장합니다.")

    # 어떤 상황(연속된 429, 서버 지연 등)에서도 이 시간을 넘기면 남은 검색어는
    # 건너뛰고 그때까지 모은 것으로 다음 단계(분석)로 넘어갑니다.
    TIME_BUDGET_SEC = 12 * 60
    start_time = time.time()

    existing: dict[str, dict] = {}
    if os.path.exists(RAW_FILE):
        with open(RAW_FILE, encoding="utf-8") as f:
            for p in json.load(f):
                existing[p["id"]] = p

    before = len(existing)
    before_analyzed = sum(1 for p in existing.values() if p.get("ai_analysis") is not None)
    print(f"[Collector] 이번 실행 시작 시점 기존 데이터: 논문 {before}건 "
          f"(그중 AI 분석 완료 {before_analyzed}건) — 이 값이 매 실행마다 유지·증가해야 정상 누적입니다.")

    # ── 체크포인트: 검색어 텍스트 기준으로 완료 여부 관리 (순서 변경/추가에 안전) ──
    completed_keywords, fail_counts = load_progress()
    save_progress(completed_keywords, fail_counts)   # git add 대상 파일이 항상 존재하도록 즉시 기록
    extra = load_extra_keywords()
    if extra:
        print(f"[Collector] EXTRA_KEYWORDS로 추가된 검색어 {len(extra)}개: {', '.join(extra)}")

    # 순서를 유지하면서 중복 제거 (기본 검색어 + 추가 검색어)
    seen = set()
    all_queries = []
    for q in QUERIES + extra:
        if q not in seen:
            seen.add(q)
            all_queries.append(q)

    pending_new = [q for q in all_queries if q not in completed_keywords]
    pending_refresh = [q for q in all_queries
                       if q in completed_keywords and days_since(completed_keywords[q]) >= REFRESH_INTERVAL_DAYS]
    # 한 번도 안 해본 새 검색어를 항상 먼저 처리하고, 그다음 재검색 주기가 된 것들을 처리합니다.
    # 각 그룹 내에서는 실패 횟수가 적은 순으로 정렬합니다.
    pending_new.sort(key=lambda q: fail_counts.get(q, 0))
    pending_refresh.sort(key=lambda q: fail_counts.get(q, 0))
    pending = pending_new + pending_refresh

    print(f"[Collector] 전체 검색어 {len(all_queries)}개 중 완료 {len(completed_keywords.keys() & seen)}개, "
          f"신규 대기 {len(pending_new)}개, 재검색 주기 도래 {len(pending_refresh)}개 "
          f"(재검색 주기: {REFRESH_INTERVAL_DAYS}일)")
    if pending:
        preview = ", ".join(f"{q[:25]}({fail_counts.get(q, 0)}회 실패)" for q in pending[:5])
        print(f"[Collector] 이번 실행 처리 순서(앞 5개): {preview}")

    stopped_early = False
    for q in pending:
        if time.time() - start_time > TIME_BUDGET_SEC:
            print(f"[Collector] 실행 시간 한도({TIME_BUDGET_SEC//60}분) 도달 — "
                  f"남은 검색어는 다음 실행에서 이어서 처리합니다.")
            stopped_early = True
            break

        is_refresh = q in completed_keywords
        print(f"[Collector] 검색: {q} ({'재검색' if is_refresh else '신규'}, 이전 실패 {fail_counts.get(q, 0)}회)")
        papers, completed = fetch_query(q, email=email, deadline=start_time + TIME_BUDGET_SEC)

        new_count = 0
        for p in papers:
            if p["id"] not in existing:
                existing[p["id"]] = p
                new_count += 1

        # 검색어 하나 처리할 때마다 즉시 저장 — 중간에 중단돼도 데이터 유실 없음
        save_raw(existing)

        if completed:
            completed_keywords[q] = today_str()
            fail_counts.pop(q, None)
            save_progress(completed_keywords, fail_counts)
            print(f"  → 완료 (신규 {new_count}건, 다음 재검색은 {REFRESH_INTERVAL_DAYS}일 후)")
        else:
            fail_counts[q] = fail_counts.get(q, 0) + 1
            save_progress(completed_keywords, fail_counts)
            print(f"  → 미완료 — 이번엔 {new_count}건 확보, 이 단어는 이번 실행에서 건너뛰고 "
                  f"다음 단어로 넘어갑니다. (누적 실패 {fail_counts[q]}회, 다음 실행에서 뒤 순서로 재시도)")
            stopped_early = True
            # break 하지 않고 계속 진행 — 실패한 단어 때문에 뒤에 남은
            # 새 단어들까지 영영 시도되지 못하는 것을 방지합니다.
            time.sleep(1)
            continue

        time.sleep(1)

    if not stopped_early and pending:
        print(f"[Collector] 이번 실행 대상 검색어를 모두 처리했습니다. "
              f"완료된 검색어는 {REFRESH_INTERVAL_DAYS}일 후 자동으로 재검색 대상이 됩니다.")

    papers = list(existing.values())
    save_raw(existing)

    state = {
        "last_run":    datetime.now().isoformat(),
        "total_papers": len(papers),
        "new_papers":  len(papers) - before,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"[Collector] 완료: 총 {len(papers)}건 (신규 {len(papers) - before}건)")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # 예상 못한 오류가 나도 Actions를 실패로 표시하지 않고 정상 종료합니다.
        # (그때까지의 raw_papers.json/progress.json은 이미 저장되어 있음)
        print(f"[Collector] 처리 중 예외 발생(무시하고 정상 종료): {exc}")
