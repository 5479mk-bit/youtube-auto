"""주제 엔진 개편(2026-10-27): 기념일·스페인어권 인기도 우선, 스페인어 원문, 발굴은 지연 실행."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import yaml

from src import pipeline
from src.disasters import collect_candidates
from src.disasters import popularity as topic_rank
from src.heritage import wikipedia_enrich
from src.settings import CONFIG_DIR, Settings
from src.topics import topic_scorer


def test_parse_entry_supports_plain_es_and_alias_formats():
    plain = topic_rank.parse_entry("노트르담 대성당 화재")
    assert (plain.key, plain.lang, plain.title, plain.when) == ("노트르담 대성당 화재", "ko", "노트르담 대성당 화재", None)

    es = topic_rank.parse_entry("es:Tragedia de Armero | 1985-11-13")
    assert (es.key, es.lang, es.title, es.when) == ("Tragedia de Armero", "es", "Tragedia de Armero", date(1985, 11, 13))

    alias = topic_rank.parse_entry("타이타닉호 | 1912-04-15 | es:Hundimiento del RMS Titanic")
    # 이력 키는 한국어 제목 그대로 — 이미 다룬 주제가 다시 나오면 안 된다.
    assert alias.key == "타이타닉호"
    assert (alias.lang, alias.title, alias.fallback_title) == ("es", "Hundimiento del RMS Titanic", "타이타닉호")
    assert alias.es_title == "Hundimiento del RMS Titanic"

    bad_date = topic_rank.parse_entry("es:Apolo 1 | 1967-13-40")
    assert bad_date.when is None and bad_date.title == "Apolo 1"


def test_every_configured_title_parses_with_unique_keys():
    config = yaml.safe_load((CONFIG_DIR / "config.yaml").read_text(encoding="utf-8"))
    titles = config["topics"]["disaster"]["titles"]
    entries = [topic_rank.parse_entry(item) for item in titles]
    assert all(entry.key and entry.title for entry in entries)
    assert len({entry.key for entry in entries}) == len(entries)
    assert sum(1 for entry in entries if entry.lang == "es") >= 50
    assert sum(1 for entry in entries if entry.when) >= 70
    assert set(config["topics"]["disaster"]["weights"]) == {"anniversary", "popularity", "audience_fit", "story_density"}


def test_audience_today_uses_latam_date():
    # KST 2026-10-28 12:30 = UTC 03:30 = 멕시코시티 10/27 21:30
    now = datetime(2026, 10, 28, 3, 30, tzinfo=timezone.utc)
    assert topic_rank.audience_today(now) == date(2026, 10, 27)


def test_anniversary_score_prefers_the_day_and_just_before():
    when = date(1985, 11, 13)
    assert topic_rank.anniversary_score(when, date(2026, 11, 13)) == 1.0
    assert topic_rank.anniversary_score(when, date(2026, 11, 11)) == 0.9
    assert topic_rank.anniversary_score(when, date(2026, 11, 9)) == 0.5
    assert topic_rank.anniversary_score(when, date(2026, 11, 14)) == 0.4
    assert topic_rank.anniversary_score(when, date(2026, 11, 7)) == 0.2
    assert topic_rank.anniversary_score(when, date(2026, 8, 1)) == 0.0
    assert topic_rank.anniversary_score(None, date(2026, 11, 13)) == 0.0
    # 연말·연초를 걸쳐도 셈이 맞아야 한다.
    assert topic_rank.anniversary_score(date(2004, 12, 26), date(2026, 12, 25)) == 0.9
    assert topic_rank.anniversary_score(date(2012, 1, 1), date(2026, 12, 31)) == 0.9
    # 2월 29일 사건은 평년에 3월 1일로 센다.
    assert topic_rank.anniversary_score(date(2024, 2, 29), date(2027, 3, 1)) == 1.0


def test_anniversary_hint_gives_exact_years_without_today_wording():
    hint = topic_rank.anniversary_hint(date(1985, 11, 13), date(2026, 11, 12))
    assert "13 de noviembre de 2026" in hint
    assert "41 años" in hint and "Hace 41 años" in hint
    assert "hoy" not in hint.lower()
    assert topic_rank.anniversary_hint(date(1985, 11, 13), date(2026, 6, 1)) == ""


def test_collect_candidates_fetches_anniversary_first_from_spanish_wikipedia():
    calls = []

    def fake_fetch(title, lang="ko"):
        calls.append((title, lang))
        return "Texto del artículo.\n\nOtro párrafo." * 30

    titles = [
        "노트르담 대성당 화재 | 2019-04-15",
        "es:Tragedia de Armero | 1985-11-13",
        "es:Huracán Mitch | 1998-10-29",
    ]
    weights = {"anniversary": 0.45, "popularity": 0.4, "story_density": 0.15}
    with patch("src.disasters.wikipedia_enrich.fetch_full_text", side_effect=fake_fetch), \
         patch("src.disasters.time.sleep"):
        candidates = collect_candidates(
            titles, set(), (), 1, today=date(2026, 10, 27), weights=weights,
            popularity={"노트르담 대성당 화재": 0.9},
        )

    # 이틀 뒤가 기념일인 미치가 인기도 높은 노트르담보다 먼저, 본문 조회는 1번만.
    assert calls == [("Huracán Mitch", "es")]
    mitch = candidates[0]
    assert mitch.topic_key == "disaster:Huracán Mitch"
    assert mitch.source_url == "https://es.wikipedia.org/wiki/Hurac%C3%A1n_Mitch"
    assert mitch.features["anniversary"] == 0.9
    assert any("Hace 28 años" in tag for tag in mitch.topic_tags)


def test_collect_candidates_without_anniversary_follows_popularity_then_config_order():
    def fake_fetch(title, lang="ko"):
        return "본문 문단.\n\n둘째 문단." * 40

    titles = ["말파세댐 붕괴 사고", "타이타닉호 | es:Hundimiento del RMS Titanic", "폼페이"]
    weights = {"anniversary": 0.45, "popularity": 0.4, "story_density": 0.15}
    with patch("src.disasters.wikipedia_enrich.fetch_full_text", side_effect=fake_fetch), \
         patch("src.disasters.time.sleep"):
        candidates = collect_candidates(
            titles, set(), (), 2, today=date(2026, 6, 1), weights=weights,
            popularity={"타이타닉호": 0.95, "말파세댐 붕괴 사고": 0.3},
        )
    assert [c.topic_key for c in candidates] == ["disaster:타이타닉호", "disaster:말파세댐 붕괴 사고"]
    picked = topic_scorer.pick(candidates, weights, set())
    assert picked.topic_key == "disaster:타이타닉호"


def test_collect_candidates_falls_back_to_korean_when_spanish_page_fails():
    calls = []

    def fake_fetch(title, lang="ko"):
        calls.append((title, lang))
        return "" if lang == "es" else "한국어 본문." * 50

    with patch("src.disasters.wikipedia_enrich.fetch_full_text", side_effect=fake_fetch), \
         patch("src.disasters.time.sleep"):
        candidates = collect_candidates(["타이타닉호 | es:Hundimiento del RMS Titanic"])

    assert calls == [("Hundimiento del RMS Titanic", "es"), ("타이타닉호", "ko")]
    assert candidates[0].topic_key == "disaster:타이타닉호"
    assert candidates[0].headline == "타이타닉호"
    assert candidates[0].source_url.startswith("https://ko.wikipedia.org/wiki/")


def test_collect_candidates_skips_used_alias_keys():
    with patch("src.disasters.wikipedia_enrich.fetch_full_text", return_value="본문" * 100) as fetch, \
         patch("src.disasters.time.sleep"):
        candidates = collect_candidates(
            ["타이타닉호 | es:Hundimiento del RMS Titanic", "es:Apolo 1 | 1967-01-27"],
            used={"disaster:타이타닉호"},
        )
    fetch.assert_called_once_with("Apolo 1", "es")
    assert [c.topic_key for c in candidates] == ["disaster:Apolo 1"]


def _response(status=200, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload or {}
    response.raise_for_status.return_value = None
    return response


def test_popularity_scores_uses_langlinks_pageviews_and_cache(tmp_path):
    cache = tmp_path / "pop.json"
    entries = [
        topic_rank.parse_entry("es:Tragedia de Armero | 1985-11-13"),
        topic_rank.parse_entry("간토 대지진 | 1923-09-01"),
        topic_rank.parse_entry("무명 사건"),
    ]
    langlinks = _response(payload={"query": {"pages": [
        {"title": "간토 대지진", "langlinks": [{"lang": "es", "title": "Gran terremoto de Kantō"}]},
        {"title": "무명 사건"},
    ]}})
    armero = _response(payload={"items": [{"views": 500}] * 60})
    kanto = _response(payload={"items": [{"views": 20}] * 60})

    with patch("src.disasters.popularity.requests.get", side_effect=[langlinks, armero, kanto]) as get, \
         patch("src.disasters.popularity.time.sleep"):
        scores = topic_rank.popularity_scores(entries, cache, date(2026, 10, 27))

    assert get.call_count == 3
    assert "es.wikipedia/all-access/user/Tragedia_de_Armero/daily/20260828/20261026" in get.call_args_list[1].args[0]
    assert scores["Tragedia de Armero"] > scores["간토 대지진"] > 0
    assert "무명 사건" not in scores  # 스페인어판이 없다 = 중남미가 모르는 사건
    assert topic_rank.es_source_of("간토 대지진", cache) == "Gran terremoto de Kantō"

    # 14일 안에는 캐시만 쓴다 — 매 회차 API 를 두드리지 않는다.
    with patch("src.disasters.popularity.requests.get") as get_again:
        again = topic_rank.popularity_scores(entries, cache, date(2026, 11, 2))
    get_again.assert_not_called()
    assert again == scores


def test_popularity_scores_stops_on_429(tmp_path):
    entries = [topic_rank.parse_entry(f"es:Doc {i}") for i in range(5)]
    with patch("src.disasters.popularity.requests.get", return_value=_response(429)) as get, \
         patch("src.disasters.popularity.time.sleep"):
        scores = topic_rank.popularity_scores(entries, tmp_path / "pop.json", date(2026, 10, 27))
    assert scores == {}
    assert get.call_count == 1


def test_popularity_score_is_log_scaled():
    assert topic_rank.popularity_score(0) == 0.0
    assert 0.4 < topic_rank.popularity_score(300) < 0.6
    assert topic_rank.popularity_score(100_000) == 1.0


def _settings(titles, discover_enabled=True):
    return Settings(
        {
            "topics": {
                "source": "disaster",
                "disaster": {
                    "titles": titles,
                    "weights": {"anniversary": 0.45, "popularity": 0.4, "story_density": 0.15},
                    "discover": {"enabled": discover_enabled, "roots": ["분류:재난"], "per_run_candidates": 3},
                },
            }
        },
        secrets=None,
    )


def test_discovery_crawl_runs_only_when_curated_list_is_exhausted():
    # run #82: 지정 주제가 남았는데도 매번 분류 트리를 먼저 크롤링하다 429 를 17분 맞았다.
    with patch.object(pipeline.topic_rank, "popularity_scores", return_value={}), \
         patch.object(pipeline.disaster_discovery, "discover_titles") as discover, \
         patch("src.disasters.wikipedia_enrich.fetch_full_text", return_value="본문." * 200), \
         patch("src.disasters.time.sleep"):
        candidates, _ = pipeline._disaster_candidates(_settings(["es:Apolo 1 | 1967-01-27"]), set())
    discover.assert_not_called()
    assert candidates[0].topic_key == "disaster:Apolo 1"


def test_discovery_prefers_titles_with_spanish_article(tmp_path):
    def fake_scores(entries, cache_path, today):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        cache_path.write_text(json.dumps({
            "ko2es:유명 지진": {"title": "Terremoto famoso", "at": today.isoformat()},
            "ko2es:무명 지진": {"title": "", "at": today.isoformat()},
        }), encoding="utf-8")
        return {"유명 지진": 0.8} if any(e.key == "유명 지진" for e in entries) else {}

    calls = []

    def fake_fetch(title, lang="ko"):
        calls.append((title, lang))
        return "Texto." * 200

    with patch.object(pipeline, "CACHE_DIR", tmp_path), \
         patch.object(pipeline.topic_rank, "popularity_scores", side_effect=fake_scores), \
         patch.object(pipeline.disaster_discovery, "discover_titles", return_value=["무명 지진", "유명 지진"]), \
         patch("src.disasters.wikipedia_enrich.fetch_full_text", side_effect=fake_fetch), \
         patch("src.disasters.time.sleep"):
        candidates, _ = pipeline._disaster_candidates(
            _settings(["es:Apolo 1 | 1967-01-27"]), {"disaster:Apolo 1"}
        )
    assert calls[0] == ("Terremoto famoso", "es")
    assert candidates[0].topic_key == "disaster:유명 지진"


def test_wikipedia_requests_identify_with_contact_and_language():
    assert "github.com/5479mk-bit/youtube-auto" in wikipedia_enrich.HEADERS["User-Agent"]
    ok = _response(payload={"query": {"pages": {"1": {"extract": "texto"}}}})
    with patch("src.heritage.wikipedia_enrich.requests.get", return_value=ok) as get:
        assert wikipedia_enrich.fetch_full_text("Tragedia de Armero", "es") == "texto"
    assert get.call_args.args[0] == "https://es.wikipedia.org/w/api.php"


def test_prompt_never_claims_today_for_anniversaries():
    from src.script.script_writer import GLOBAL_DISASTER_PROMPT

    assert '"Un día como hoy, hace N años,"' not in GLOBAL_DISASTER_PROMPT
    assert 'nunca "hoy"' in GLOBAL_DISASTER_PROMPT


def test_seo_prompt_receives_anniversary_hint():
    from src.models import Script, ScriptLine, ScriptPart, StoryCandidate
    from src.publish import seo_metadata

    candidate = StoryCandidate(
        dataset="disaster", topic_key="disaster:Tragedia de Armero", headline="Tragedia de Armero",
        headline_number="", subject="Tragedia de Armero", context="", facts=["x"], source_name="Wikipedia",
    )
    candidate.topic_tags.append(topic_rank.anniversary_hint(date(1985, 11, 13), date(2026, 11, 12)))
    part = ScriptPart(
        part=1, hook_number="¿Por qué nadie huyó?", lines=[ScriptLine(text="Línea.", visual_query_ko="")]
    )
    script = Script(topic_key=candidate.topic_key, title_seed="Armero", part1=part, part2=part)
    provider = MagicMock()
    provider.complete_json.return_value = None

    seo_metadata.generate(script, part, candidate, provider, [], is_combined=True)

    prompt = provider.complete_json.call_args.args[0]
    assert "Pistas: aniversario:" in prompt and "Hace 41 años" in prompt
    assert "uno** de los 3 títulos" in prompt
