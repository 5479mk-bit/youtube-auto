"""재미 강화(2026-10-27): 흥미 장치 의무화, 지속률 편집 2차 패스, 충격 플래시."""

from __future__ import annotations

from types import SimpleNamespace

from src.models import StoryCandidate
from src.render import video_builder
from src.script import script_writer


def _candidate():
    return StoryCandidate(
        dataset="disaster", topic_key="disaster:x", headline="Tragedia", headline_number="",
        subject="Tragedia", context="texto", facts=["hecho"] * 5, source_name="Wikipedia",
    )


def _raw(prefix, n=6):
    line = lambda i: {"text": f"{prefix} frase número {i}", "scene_type": "AI_IMAGE", "prompt": "p"}
    return {"title_seed": "t", "part1": {"hook_number": "¿Qué?", "lines": [line(i) for i in range(n)]},
            "part2": {"hook_number": "", "lines": [line(i) for i in range(n)]}}


def test_prompt_requires_interest_engines():
    prompt = script_writer._prompt(_candidate(), 260, 290)
    for marker in ("MOTORES DE INTERÉS", "Una persona", "Dos bucles abiertos", "Giro final",
                   "cada frase tiene que hacer que quieran oír la siguiente"):
        assert marker in prompt


class _Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def complete_json(self, prompt, temperature):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_retention_editor_rewrite_is_used():
    provider = _Provider(_raw("Borrador"), {"scores": {"gancho": 6}, "script": _raw("Editado")})
    script = script_writer.generate(_candidate(), provider, 260, 290, 0.9)
    assert script.part1.lines[0].text.startswith("Editado")
    assert "editor de retención" in provider.prompts[1]
    assert "Borrador frase número 0" in provider.prompts[1]  # 초안을 넘겨 고치게 한다


def test_broken_editor_output_keeps_draft():
    provider = _Provider(_raw("Borrador"), {"scores": {}, "script": _raw("Corto", n=2)})
    script = script_writer.generate(_candidate(), provider, 260, 290, 0.9)
    assert script.part1.lines[0].text.startswith("Borrador")


def test_editor_exception_keeps_draft():
    class Boom(_Provider):
        def complete_json(self, prompt, temperature):
            if self.prompts:
                raise RuntimeError("429")
            return super().complete_json(prompt, temperature)

    script = script_writer.generate(_candidate(), Boom(_raw("Borrador")), 260, 290, 0.9)
    assert script.part1.lines[0].text.startswith("Borrador")


def test_impact_flash_only_on_collapse_sounds():
    settings = SimpleNamespace(get=lambda key, default=None: default)
    scenes = [SimpleNamespace(sfx=sfx, start=float(i)) for i, sfx in
              enumerate(["none", "crack", "whoosh", "braam", "sub_boom"])]
    layers = video_builder._impact_flash_layers(scenes, (108, 192), 10.0, settings)
    assert [layer.start for layer in layers] == [1.0, 3.0]
    assert all(abs(layer.duration - 0.12) < 1e-6 for layer in layers)
