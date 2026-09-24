from src.narration.tts_cloud import _build_ssml, _rate_to_multiplier


def test_build_ssml_replaces_ellipsis_with_break():
    ssml = _build_ssml("하늘을 봤는데...", -1.5)
    assert '<break time="500ms"/>' in ssml
    assert "..." not in ssml
    assert ssml.startswith('<speak><prosody pitch="-1.5st">')
    assert ssml.endswith("</prosody></speak>")


def test_build_ssml_escapes_xml_special_chars():
    ssml = _build_ssml("A & B <test>", -1.5)
    assert "&amp;" in ssml
    assert "&lt;test&gt;" in ssml


def test_rate_to_multiplier_converts_percent_strings():
    assert _rate_to_multiplier("-10%") == 0.9
    assert _rate_to_multiplier("+2%") == 1.02
    assert _rate_to_multiplier("") == 1.0
    assert _rate_to_multiplier("+0%") == 1.0


def test_rate_to_multiplier_clamps_to_valid_range():
    assert _rate_to_multiplier("-90%") == 0.25
    assert _rate_to_multiplier("+500%") == 4.0
