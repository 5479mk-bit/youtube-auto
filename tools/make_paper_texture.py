"""한지(사료) 배경 텍스처를 직접 만든다.

위키미디어에서 종이 텍스처를 받아 쓰려 했는데 지정된 파일(`Old_paper_texture.jpg`)이
존재하지 않았고, 커먼즈는 요청이 몰리면 429 로 막는다. 배경 텍스처는 사진일 필요가 없어
직접 만드는 쪽이 낫다 — 링크가 죽을 일도, 출처를 밝힐 일도 없다.

만드는 것: 누런 한지 바탕에 섬유 결(가로줄), 얼룩, 가장자리 그을림(비네트).

    python tools/make_paper_texture.py assets/textures/hanji.jpg
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

WIDTH, HEIGHT = 1080, 1920
# 오래된 닥종이 색. 너무 노랗게 하면 촌스러워서 살짝만 물린다.
BASE_RGB = (214, 198, 166)


def _blurred_noise(shape: tuple[int, int], rng: np.random.Generator, radius: float) -> np.ndarray:
    """흐린 노이즈 한 겹. **평균 0, 표준편차 1 로 정규화해서 돌려준다.**

    정규화가 핵심이다. 백색 노이즈를 흐리면 분산이 반지름에 비례해 확 줄어서, 그냥 더하면
    굵은 무늬는 거의 사라지고 단색 배경이 된다(실제로 처음엔 그렇게 나왔다).
    """
    noise = rng.normal(0.5, 0.25, shape)
    image = Image.fromarray(np.clip(noise * 255, 0, 255).astype(np.uint8))
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    std = blurred.std()
    return (blurred - blurred.mean()) / (std if std > 1e-6 else 1.0)


def build(width: int = WIDTH, height: int = HEIGHT, seed: int = 11) -> Image.Image:
    rng = np.random.default_rng(seed)

    # 얼룩은 한 겹으로는 안 된다 — 한 번 흐린 노이즈는 거의 균일한 회색이 돼서 종이가
    # 아니라 단색 배경처럼 보인다. 굵은 무늬부터 고운 무늬까지 겹쳐야 결이 산다.
    stains = np.zeros((height, width), dtype=np.float32)
    for radius, weight in ((90, 1.0), (36, 0.6), (14, 0.35), (5, 0.2)):
        stains += _blurred_noise((height, width), rng, radius) * weight

    # 섬유: 가로로 길게 늘인 결. 한지 특유의 닥섬유다.
    fibre = _blurred_noise((height, width), rng, 0.8)
    for shift in (2, 5, 9):
        fibre = np.maximum(fibre, np.roll(fibre, shift, axis=1) * 0.92)

    shade = 1.0 + stains * 0.075 + fibre * 0.04
    # 옥타브를 겹치면 네모난 덩어리가 살짝 보인다(8비트로 양자화한 노이즈를 흐린 탓).
    # 마지막에 한 번 더 부드럽게 눌러 경계를 없앤다.
    shade = np.asarray(
        Image.fromarray((np.clip(shade, 0.5, 1.5) * 170).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(2.5)
        ),
        dtype=np.float32,
    ) / 170.0

    # 비네트: 가장자리가 어두워야 오래된 종이로 보인다.
    ys = np.linspace(-1, 1, height)[:, None]
    xs = np.linspace(-1, 1, width)[None, :]
    vignette = 1.0 - 0.38 * np.clip((xs**2 + ys**2) / 1.6, 0, 1)

    base = np.array(BASE_RGB, dtype=np.float32)[None, None, :]
    pixels = base * (shade * vignette)[:, :, None]

    # 마지막에 고운 입자를 얹어야 평면처럼 안 보인다.
    pixels += rng.normal(0, 4.0, pixels.shape)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "assets/textures/hanji.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    build().save(out, quality=92)
    print(f"{out} ({WIDTH}x{HEIGHT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
