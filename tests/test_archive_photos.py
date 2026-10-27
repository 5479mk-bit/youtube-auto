"""위키미디어 공용 실제 사진(2026-10-27 사용자 결정: 공용 사진만)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from src import pipeline
from src.disasters import commons_photos
from src.models import ArchivePhoto, Scene, StoryCandidate
from src.render import motion_sources
from src.script import script_writer


def _page(title, license_name="Public domain", repo="shared", mime="image/jpeg", w=1200, h=838,
          desc="El SS Californian en 1911", artist="<bdi><a href='x'>Louis Ogden</a></bdi>"):
    meta = {"LicenseShortName": {"value": license_name}, "Artist": {"value": artist},
            "ImageDescription": {"value": desc}}
    return {"title": title, "imagerepository": repo, "imageinfo": [{
        "mime": mime, "width": w, "height": h, "thumburl": f"https://upload.wikimedia.org/{title}",
        "url": f"https://upload.wikimedia.org/orig/{title}", "extmetadata": meta}]}


def test_only_free_commons_photos_without_victims_are_kept():
    pages = [
        _page("Archivo:Californian.jpg"),
        _page("Archivo:Gustloff.jpg", license_name="CC BY-SA 3.0 de", desc="Buque en Danzig"),
        _page("Archivo:Local.jpg", repo="local"),                       # 위키 로컬(비자유 가능)
        _page("Archivo:NC.jpg", license_name="CC BY-NC 2.0"),            # 비상업 — 안 됨
        _page("Archivo:GFDL.jpg", license_name="GFDL"),                  # 단독 GFDL — 안 됨
        _page("Archivo:Cuerpos.jpg", desc="Recuperación de cuerpos de víctimas"),  # 희생자
        _page("Archivo:Funeral.jpg", desc="Funeral de los marineros"),
        _page("Archivo:Flag of UK.png", mime="image/png"),               # 사진이 아님
        _page("Archivo:Mapa.svg", mime="image/svg+xml"),
        _page("Archivo:Tiny.jpg", w=200, h=150),
    ]
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {"query": {"pages": pages}}
    with patch("src.disasters.commons_photos.requests.get", return_value=response) as get:
        photos = commons_photos.fetch_article_photos("es", "Hundimiento del RMS Titanic")
    assert [photo.title for photo in photos] == ["Archivo:Californian.jpg", "Archivo:Gustloff.jpg"]
    assert get.call_args.args[0] == "https://es.wikipedia.org/w/api.php"
    assert photos[0].author == "Louis Ogden"
    assert photos[0].credit() == "Louis Ogden, Public domain (Wikimedia Commons)"
    assert photos[1].license == "CC BY-SA 3.0 de"


def test_fetch_failure_means_no_photos():
    with patch("src.disasters.commons_photos.requests.get", side_effect=RuntimeError("down")):
        assert commons_photos.fetch_article_photos("es", "X") == []


def test_license_and_source_helpers():
    assert commons_photos.is_free_license("CC BY 4.0")
    assert commons_photos.is_free_license("CC0")
    assert commons_photos.is_free_license("Dominio público")
    assert not commons_photos.is_free_license("CC BY-ND 4.0")
    assert not commons_photos.is_free_license("Fair use")
    assert not commons_photos.is_free_license("")
    assert commons_photos.source_of("https://es.wikipedia.org/wiki/Tragedia_de_Armero") == ("es", "Tragedia de Armero")
    assert commons_photos.source_of("https://es.wikipedia.org/wiki/Hurac%C3%A1n_Mitch") == ("es", "Huracán Mitch")
    assert commons_photos.source_of("https://example.com/x") is None


def _candidate(photos):
    candidate = StoryCandidate(dataset="disaster", topic_key="disaster:x", headline="Titanic",
                               headline_number="", subject="Titanic", context="texto", facts=["x"],
                               source_name="Wikipedia")
    candidate.archive_photos = photos
    return candidate


PHOTO = ArchivePhoto(title="Archivo:JPMorgan.jpg", url="https://upload.wikimedia.org/jpm.jpg",
                     description="Retrato de J. P. Morgan", author="Pach Brothers", license="Public domain")


def test_prompt_lists_photos_and_parse_maps_number_to_photo():
    prompt = script_writer._prompt(_candidate([PHOTO]), 260, 290)
    assert "1. Retrato de J. P. Morgan" in prompt and '"FOTO"' in prompt
    assert "ninguna" in script_writer._prompt(_candidate([]), 260, 290)

    line = lambda text, **extra: {"text": text, "scene_type": "AI_IMAGE", "prompt": "p", **extra}
    raw = {"title_seed": "t", "part1": {"hook_number": "¿Qué?", "lines": [
        line("Una frase de video muy larga", scene_type="FOTO", foto=1),  # 첫 문장 → Veo 로
        line("J. P. Morgan canceló su boleto", scene_type="FOTO", foto=1),
        line("Otra foto que no existe", scene_type="FOTO", foto=7),        # 없는 번호 → 생성 그림
        *[line(f"Normal {i}") for i in range(4)]]},
        "part2": {"hook_number": "", "lines": [line(f"Normal {i}") for i in range(6)]}}
    script = script_writer._parse(raw, _candidate([PHOTO]))
    first, morgan, missing = script.part1.lines[:3]
    assert first.scene_type == "VEO" and first.photo_url == ""
    assert morgan.scene_type == "FOTO" and morgan.photo_url == PHOTO.url
    assert morgan.photo_credit == "Pach Brothers, Public domain (Wikimedia Commons)"
    assert missing.scene_type == "AI_IMAGE" and missing.photo_url == ""


def test_attach_archive_photos_uses_candidate_wiki_page():
    candidate = _candidate([])
    candidate.source_url = "https://es.wikipedia.org/wiki/Hundimiento_del_RMS_Titanic"
    settings = SimpleNamespace(get=lambda key, default=None: {
        "visuals.archive_photos.enabled": True, "visuals.archive_photos.max": 5}.get(key, default))
    with patch("src.disasters.commons_photos.fetch_article_photos", return_value=[PHOTO]) as fetch:
        pipeline._attach_archive_photos(settings, candidate)
    fetch.assert_called_once_with("es", "Hundimiento del RMS Titanic", limit=5)
    assert candidate.archive_photos == [PHOTO]


def test_compose_vertical_keeps_whole_landscape_photo(tmp_path):
    photo = tmp_path / "wide.jpg"
    Image.new("RGB", (1200, 800), (200, 30, 30)).save(photo)
    out = commons_photos.compose_vertical(photo, tmp_path / "v.jpg", (1080, 1920))
    framed = Image.open(out)
    assert framed.size == (1080, 1920)
    # 가운데 줄은 원본(빨강), 맨 아래는 흐리고 어두운 배경
    r, g, b = framed.getpixel((540, int(1920 * 0.32)))
    assert r > 150 and g < 80
    assert sum(framed.getpixel((540, 1900))) < sum((200, 30, 30))


def test_photo_scene_falls_back_to_ai_image_when_download_fails():
    scene = Scene(start=0, duration=2, image_query_ko="", image_query_en="", camera="push_in",
                  engine="archive_photo", scene_type="FOTO", prompt="p", photo_url="https://x/y.jpg",
                  photo_credit="A, Public domain (Wikimedia Commons)")
    with patch("src.disasters.commons_photos.download", return_value=None), \
         patch.object(motion_sources, "_build_generated_clip", return_value=("clip", "credit")) as generated:
        result = motion_sources.build_scene_clip(scene, None, None, (1080, 1920), 0.1, 0.1)
    assert result == ("clip", "credit")
    assert generated.call_args.args[0].engine == "ai_image"


def test_photo_scene_credits_commons_author(tmp_path):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (800, 1200), (90, 90, 90)).save(photo)
    scene = Scene(start=0, duration=1, image_query_ko="", image_query_en="", camera="pan_lr",
                  engine="archive_photo", scene_type="FOTO", photo_url="https://x/p.jpg",
                  photo_credit="Pach Brothers, Public domain (Wikimedia Commons)")
    with patch("src.disasters.commons_photos.download", return_value=photo):
        clip, credit = motion_sources.build_scene_clip(scene, None, None, (108, 192), 0.1, 0.1)
    assert credit.source_name == "Pach Brothers, Public domain (Wikimedia Commons)"
    assert clip.get_frame(0.5).shape == (192, 108, 3)


def test_long_credit_banner_wraps_inside_screen(tmp_path):
    from src.render import overlays

    text = ("Fotos: Imagen generada por IA, Video generado por IA, Pach Brothers, Public domain "
            "(Wikimedia Commons), Sönnke, Hans, CC BY-SA 3.0 de (Wikimedia Commons)")
    path = overlays.render_banner(text, tmp_path / "b.png", (1080, 1920), 26, 0.72)
    alpha = Image.open(path).split()[-1]
    left, top, right, bottom = alpha.getbbox()
    assert left > 0 and right < 1080  # 잘리지 않는다
    assert bottom - top > 26 * 1.5   # 여러 줄로 접혔다
