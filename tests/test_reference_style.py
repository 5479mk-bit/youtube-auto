"""레퍼런스(틱톡 lazarus_404 등, 2026-10-27) 반영: '¿Y si…?' 오프닝·이론은 이론으로, 미스터리 문단,
단어별 하이라이트 자막."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from PIL import Image

from src.disasters import MAX_CONTEXT_CHARS, _mystery_appendix, collect_candidates
from src.models import ScriptLine, ScriptPart, StoryCandidate
from src.render import overlays
from src.script import script_writer
from src.subtitles.subtitle_builder import SubtitleCue, build_cues


def _candidate():
    return StoryCandidate(dataset="disaster", topic_key="disaster:x", headline="Titanic",
                          headline_number="", subject="Titanic", context="texto", facts=["x"],
                          source_name="Wikipedia")


def test_prompt_offers_what_if_opening_and_theories_only_as_theories():
    prompt = script_writer._prompt(_candidate(), 260, 290)
    assert "¿Y si el Titanic no se hundió por un accidente?" in prompt
    assert "Siempre como teoría, nunca como hecho" in prompt
    assert "no la inventes" in prompt
    assert "vacunas" in prompt  # 위험한 음모론은 금지
    assert "¿Tú qué crees?" in prompt  # 댓글을 부르는 마지막 질문


def test_mystery_paragraphs_from_late_in_article_are_appended():
    head = "Párrafo inicial del artículo. " * (MAX_CONTEXT_CHARS // 29 + 5)
    theory = ("Existen varias teorías de la conspiración que afirman que el hundimiento fue "
              "planeado por banqueros de la época.")
    boring = "La reconstrucción del puerto se completó en 1915 con fondos públicos del gobierno local."
    appendix = _mystery_appendix(head + "\n" + boring + "\n" + theory)
    assert "Teorías, misterios y advertencias" in appendix
    assert theory in appendix and boring not in appendix
    assert _mystery_appendix("corto") == ""


def test_candidate_context_includes_mystery_appendix():
    text = "Inicio. " * 600 + "\nNunca se supo por qué el capitán ignoró las seis advertencias de hielo recibidas."
    with patch("src.disasters.wikipedia_enrich.fetch_full_text", return_value=text), \
         patch("src.disasters.time.sleep"):
        candidate = collect_candidates(["es:Titanic"])[0]
    assert "ignoró las seis advertencias" in candidate.context


def test_word_highlight_splits_chunks_by_word():
    part = ScriptPart(part=1, hook_number="", lines=[
        ScriptLine(text="Y si el Titanic", visual_query_ko="", duration=2.0)])
    cues = build_cues(part, 30, word_highlight=True)
    assert [cue.active_word for cue in cues] == [0, 1, 2, 3]
    assert all(cue.lines == ["Y si el Titanic"] for cue in cues)
    assert cues[0].start == 0 and abs(cues[-1].end - 2.0) < 1e-9
    assert cues[3].end - cues[3].start > cues[0].end - cues[0].start  # 긴 단어가 더 오래
    plain = build_cues(part, 30)
    assert len(plain) == 1 and plain[0].active_word == -1


def test_active_word_gets_colored_box_and_uppercase(tmp_path):
    style = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))["subtitle"]
    assert style["uppercase"] is True and style["word_highlight"] is True
    cue = SubtitleCue(0, 1, ["y si el Titanic"], ["Titanic"], active_word=1)
    path, _ = overlays.render_subtitle(cue, tmp_path / "s.png", (1080, 1920), style, 0)
    image = Image.open(path).convert("RGBA")
    box = (227, 36, 91)
    pixels = [image.getpixel((x, y))[:3] for x in range(0, image.width, 2) for y in range(0, image.height, 2)]
    assert sum(1 for p in pixels if p == box) > 200  # 빨간 박스가 깔렸다
    assert sum(1 for p in pixels if p == (255, 216, 77)) > 20  # 강조어(TITANIC)는 노란색


def test_subtitle_layers_keep_position_for_non_pop_cues(tmp_path):
    from src.render import video_builder

    cfg = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))

    class Settings:
        def __getitem__(self, key):
            return cfg[key]

        def get(self, key, default=None):
            node = cfg
            for piece in key.split("."):
                if not isinstance(node, dict) or piece not in node:
                    return default
                node = node[piece]
            return node

    cues = [SubtitleCue(0, 0.5, ["y si el Titanic"], [], 0), SubtitleCue(0.5, 1, ["y si el Titanic"], [], 1)]
    layers = video_builder._subtitle_layers(cues, tmp_path, 1, (1080, 1920), Settings())
    second = layers[1]
    x, y = second.pos(0.6)
    assert x > 50 and y > 1920 * 0.5  # 왼쪽 위(0,0)가 아니라 화면 아래쪽 가운데
