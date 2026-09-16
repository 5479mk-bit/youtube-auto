"""국가유산청 국가문화유산포털 — 문화재 목록과 해설문.

야사 콘텐츠의 1차 소스다. 해설문은 정식 설명이라 사실관계는 믿을 수 있지만, 그 자체로는
"이야기"가 아닐 수 있어 위키백과 보강(wikipedia_enrich)과 합쳐서 쓴다.

khs.go.kr 는 apis.data.go.kr 포털과 다른 별도 호스트다 — 실제로 호출해본 결과 **인증키가
필요 없고 응답은 항상 XML** 이라, 여기서는 `datasource/datagokr.DataGoKrClient`(JSON 전제)
대신 `khs_client`를 쓴다. `ccbaMnm1`에 검색어(예: "첨성대")를 넣는 이름 검색은 실제로는
결과가 비었다 — 대신 `ccbaKdcd`(지정 등급 종목코드)로 걸러야 목록이 온다. `KDCD_LABELS`의
"11"(국보)은 직접 호출로 확인했고, 나머지 코드는 국가유산청이 공개한 종목코드 체계를 따른
추정치다 — 실제 응답과 다르면 여기만 고치면 된다(통계 어댑터를 쓸 때와 같은 패턴).

상세 API(`ccbaKdcd`+`ccbaAsno`+`ccbaCtcd` 조합, 실제 예시로 확인)의 item 필드:
`ccmaName`(카테고리, 예: "국보" — 목록 API 엔 없는 필드라 상세를 받으면 이걸로 갱신한다),
`ccbaMnm1`/`ccbaMnm2`(국문/한문 명칭), `ccbaAsdt`(지정일자 — 날짜 문자열이지 해설문이
아니다, 예: "19621220"), `ccbaLcad`(소재지 상세 주소), `ccceName`(시대), `content`(해설문
본문, 실제로 약 500자 분량 확인), `imageUrl`(대표 이미지 하나).
"""

from __future__ import annotations

from ..models import StoryCandidate
from . import khs_client
from .story_scorer import extract_facts, mystery_score, prestige_score, story_density

# 실제 freshness(최근에 다룬 시대·지역과 겹치는지)는 history 를 참조하는 pipeline 쪽에서
# 채운다. 여기서는 "아직 아무 회차와도 비교 전" 이라는 중립값을 준다.
_DEFAULT_FRESHNESS = 1.0

LIST_ENDPOINT = "http://www.khs.go.kr/cha/SearchKindOpenapiList.do"
DETAIL_ENDPOINT = "http://www.khs.go.kr/cha/SearchKindOpenapiDt.do"
SOURCE_NAME = "국가유산청 국가문화유산포털"
SOURCE_URL = "https://www.heritage.go.kr"

# 종목코드 → 표시 라벨. "11"(국보)만 직접 호출로 확인했고 나머지는 추정치다.
KDCD_LABELS = {
    "11": "국보",
    "12": "보물",
    "13": "사적",
    "15": "명승",
    "16": "천연기념물",
    "17": "국가무형유산",
}

# 시대·지역이 겹치지 않게 훑을 기본 종목코드. 페이지네이션(pageUnit/pageIndex)은
# 최소 동작을 위해 아직 안 쓴다 — 첫 페이지만 받는다.
DEFAULT_KDCDS = list(KDCD_LABELS.keys())


class HeritageSource:
    name = "heritage"

    def fetch(self, limit_per_kdcd: int = 8) -> list[StoryCandidate]:
        candidates: list[StoryCandidate] = []
        for kdcd in DEFAULT_KDCDS:
            try:
                rows = khs_client.get(LIST_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaCtcd": ""})
            except khs_client.KhsApiError:
                continue

            for row in rows[:limit_per_kdcd]:
                candidate = self._row_to_candidate(row)
                if candidate:
                    candidates.append(candidate)
        return candidates

    def fetch_detail(
        self, name: str, category: str, region: str, kdcd: str = "", asno: str = "", ctcd: str = ""
    ) -> StoryCandidate | None:
        """목록에서 고른 항목의 해설문을 상세 API 로 채운다."""
        try:
            rows = khs_client.get(
                DETAIL_ENDPOINT, {"ccbaKdcd": kdcd, "ccbaAsno": asno, "ccbaCtcd": ctcd}
            )
        except khs_client.KhsApiError:
            return None
        if not rows:
            return None

        row = rows[0]
        explanation = str(row.get("content") or "").strip()
        # ccmaName 은 상세 API 에만 있는 정확한 카테고리명이다. 목록에서 넘어온
        # KDCD_LABELS 기반 category 보다 이게 있으면 이걸 우선한다.
        category = str(row.get("ccmaName") or "").strip() or category
        region = str(row.get("ccbaLcad") or region or "").strip()
        kdcd = str(row.get("ccbaKdcd") or kdcd or "").strip()
        asno = str(row.get("ccbaAsno") or asno or "").strip()
        ctcd = str(row.get("ccbaCtcd") or ctcd or "").strip()
        return self._build_candidate(name, category, region, explanation, kdcd, asno, ctcd)

    def _row_to_candidate(self, row: dict) -> StoryCandidate | None:
        name = str(row.get("ccbaMnm1") or row.get("name") or "").strip()
        if not name:
            return None
        kdcd = str(row.get("ccbaKdcd") or "").strip()
        category = KDCD_LABELS.get(kdcd, str(row.get("ccmaName") or "").strip())
        region = str(row.get("ccbaCtcdNm") or row.get("region") or "").strip()
        # 목록 API 엔 해설문이 없다(ccbaMnm1/ccbaKdcd/ccbaCtcd 등 기본 정보만 옴) —
        # 해설문은 fetch_detail() 이 상세 API 로 따로 채운다.
        explanation = str(row.get("content") or "").strip()
        # 이미지·동영상·나레이션 API 는 이름이 아니라 이 세 값의 조합으로 조회한다.
        asno = str(row.get("ccbaAsno") or "").strip()
        ctcd = str(row.get("ccbaCtcd") or "").strip()
        return self._build_candidate(name, category, region, explanation, kdcd, asno, ctcd)

    def _build_candidate(
        self,
        name: str,
        category: str,
        region: str,
        explanation: str,
        kdcd: str = "",
        asno: str = "",
        ctcd: str = "",
    ) -> StoryCandidate:
        facts = extract_facts(explanation)
        return StoryCandidate(
            dataset=self.name,
            topic_key=f"heritage:{name}",
            headline=name,
            headline_number=category or "문화유산",
            subject=name,
            context=explanation,
            source_name=SOURCE_NAME,
            source_url=SOURCE_URL,
            image_queries_ko=[name, f"{name} {region}".strip(), region or name],
            image_queries_en=[name],
            facts=facts,
            heritage_ccba_kdcd=kdcd,
            heritage_ccba_asno=asno,
            heritage_ccba_ctcd=ctcd,
            features={
                "story_density": story_density(explanation),
                "mystery_score": mystery_score(explanation),
                "prestige": prestige_score(category),
                "freshness": _DEFAULT_FRESHNESS,
            },
        )
