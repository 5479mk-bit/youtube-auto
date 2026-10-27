"""2026-10-27 유튜브 스튜디오 분석 반영: 첫 화면 숫자, 시청자 적합도, 숫자형 제목, 명료한 템포."""

from __future__ import annotations

from pathlib import Path

import yaml
from PIL import Image

from src.disasters import popularity as topic_rank
from src.models import Script, ScriptPart, StoryCandidate
from src.publish import seo_metadata
from src.render import overlays
from src.script import script_writer


def _candidate(context="Murieron 69.227 personas y el sismo fue de magnitud 8.0."):
    return StoryCandidate(
        dataset="disaster", topic_key="disaster:x", headline="Terremoto", headline_number="",
        subject="Terremoto", context=context, facts=["hecho"], source_name="Wikipedia",
    )


def _raw(stat):
    line = lambda i: {"text": f"Frase dramática número {i}", "scene_type": "AI_IMAGE", "prompt": "p"}
    return {"title_seed": "t",
            "part1": {"hook_number": "¿Por qué?", "hook_stat": stat, "lines": [line(i) for i in range(6)]},
            "part2": {"hook_number": "", "hook_stat": "999 MUERTOS", "lines": [line(i) for i in range(6)]}}


def test_hook_stat_kept_only_when_number_is_in_source():
    script = script_writer._parse(_raw("69.227 muertos"), _candidate())
    assert script.part1.hook_stat == "69.227 MUERTOS"
    assert script.part2.hook_stat == ""  # 1부 것만 쓴다
    invented = script_writer._parse(_raw("500.000 MUERTOS"), _candidate())
    assert invented.part1.hook_stat == ""  # 원문에 없는 숫자는 첫 화면에 못 박는다
    too_long = script_writer._parse(_raw("69.227 personas murieron aquel día"), _candidate())
    assert too_long.part1.hook_stat == ""
    no_number = script_writer._parse(_raw("CATÁSTROFE"), _candidate())
    assert no_number.part1.hook_stat == ""


def test_hook_stat_survives_script_roundtrip_and_merge():
    from src import pipeline

    script = script_writer._parse(_raw("8.0 GRADOS"), _candidate())
    restored = Script.from_dict(script.to_dict())
    assert restored.part1.hook_stat == "8.0 GRADOS"
    merged = pipeline._merge_parts(restored)
    assert merged.hook_stat == "8.0 GRADOS"
    assert pipeline._youtube_cut(merged).hook_stat == "8.0 GRADOS"


def test_prompt_asks_for_hook_stat():
    prompt = script_writer._prompt(_candidate(), 260, 290)
    assert '"hook_stat"' in prompt and "desde el fotograma 0" in prompt


def test_hook_frame_draws_big_yellow_number_above_question(tmp_path):
    path = overlays.render_hook_question("¿Por qué nadie huyó?", tmp_path / "h.png", (540, 960),
                                         stat="200.000 MUERTOS")
    image = Image.open(path).convert("RGBA")
    yellow_rows = [
        y for y in range(960)
        if any(image.getpixel((x, y))[:3] == (255, 216, 77) and image.getpixel((x, y))[3] > 200
               for x in range(0, 540, 3))
    ]
    white_rows = [
        y for y in range(960)
        if any(image.getpixel((x, y))[:3] == (255, 255, 255) and image.getpixel((x, y))[3] > 200
               for x in range(0, 540, 3))
    ]
    assert yellow_rows and white_rows
    assert max(yellow_rows) < min(white_rows)  # 숫자가 질문 위
    # 화면 위·가운데 덩어리 — 아래쪽(58% 이하)은 첫 문장 자막 자리라 비워 둔다.
    assert 100 < min(yellow_rows) and max(white_rows) < 960 * 0.58


def test_audience_fit_prefers_mexico_earthquakes_and_flights():
    fit = lambda raw: topic_rank.audience_fit(topic_rank.parse_entry(raw))
    assert fit("es:Terremoto de Chiapas de 2017 | 2017-09-07") == 1.0
    assert fit("2008년 쓰촨 대지진 | 2008-05-12") == 1.0
    assert fit("es:Huracán Patricia | 2015-10-23") == 1.0
    assert fit("es:Vuelo 447 de Air France | 2009-06-01") == 0.8
    assert fit("es:Tragedia de Armero | 1985-11-13") == 0.7
    assert fit("es:Desastre de Seveso | 1976-07-10") == 0.0


def test_config_reflects_studio_findings():
    config = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    assert config["audio"]["tempo"] == 1.05
    assert "bien articulada" in config["audio"]["gemini_style_prompt"]
    keys = [topic_rank.parse_entry(t).key for t in config["topics"]["disaster"]["titles"]]
    assert "Terremoto de Chiapas de 2017" in keys and "Paricutín" in keys
    assert config["topics"]["disaster"]["weights"]["audience_fit"] > 0


def test_title_rules_put_number_formula_first():
    rules = seo_metadata._COMMON_RULES_ES
    assert "el primero siempre es la" in rules and "fórmula de cifra" in rules
    assert "temblor" in rules


def test_reference_style_mystery_topics_and_fit():
    config = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    keys = [topic_rank.parse_entry(t).key for t in config["topics"]["disaster"]["titles"]]
    for title in ("Tormenta solar de 1859", "Cráter de Chicxulub", "Vuelo 19", "Triángulo de las Bermudas"):
        assert title in keys
    fit = lambda raw: topic_rank.audience_fit(topic_rank.parse_entry(raw))
    assert fit("es:Cráter de Chicxulub") == 1.0
    assert fit("es:Tormenta solar de 1859 | 1859-09-01") == 0.8
