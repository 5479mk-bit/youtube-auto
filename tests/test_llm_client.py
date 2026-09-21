"""대본 공급자 선택 규칙.

여기서 잘못 갈리면 대본이 통째로 템플릿으로 떨어지는데, 그건 22분짜리 렌더가 끝난
뒤에야 눈에 띈다. 싸게 잡을 수 있는 자리라 테스트로 막아둔다.
"""

from types import SimpleNamespace

from src.script.llm_client import (
    GeminiProvider,
    VertexGeminiProvider,
    _model_candidates,
    build_provider,
)

VERTEX = object()
AUTH = object()


def _secrets(keys=("key-1",)):
    return SimpleNamespace(gemini_api_keys=list(keys), openai_api_key="", openai_base_url="")


def test_vertex_가_있으면_크레딧_경로로_간다():
    provider = build_provider("gemini", "gemini-3.6-flash", _secrets(), VERTEX, AUTH)
    assert isinstance(provider, VertexGeminiProvider)


def test_vertex_가_없으면_키로_내려간다():
    provider = build_provider("vertex_gemini", "gemini-3.6-flash", _secrets())
    assert isinstance(provider, GeminiProvider)


def test_키도_vertex_도_없으면_템플릿으로():
    assert build_provider("vertex_gemini", "", _secrets(keys=())) is None


def test_모델이_없을_때_내려갈_후보가_붙는다():
    assert _model_candidates("gemini-3.6-flash") == ["gemini-3.6-flash", "gemini-2.5-flash"]
    # 이미 폴백 모델이면 같은 이름을 두 번 시도하지 않는다.
    assert _model_candidates("gemini-2.5-flash") == ["gemini-2.5-flash"]
