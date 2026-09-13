"""세로 쇼츠용 데이터 차트 렌더.

크기 비교가 목적이므로 가로 막대를 쓴다. 한국어 항목명이 길어도 가로 막대는
회전 없이 읽히고, 9:16 화면에서 위아래로 쌓기 좋다.

색은 순위가 아니라 '주인공'을 따른다. 강조 대상 하나만 색을 갖고 나머지는 중립
회색이며, 모든 막대에 이름과 값을 직접 붙여 정체가 색에만 실리지 않게 한다.
차트는 내레이션에 맞춰 한 항목씩 드러나므로, 아직 등장하지 않은 항목도 옅은 트랙으로
자리를 잡아 레이아웃이 흔들리지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from ..models import DataPoint
from .fonts import ensure_korean_font

SURFACE = "#1a1a19"
ACCENT = "#3987e5"  # 강조 대상
MUTED = "#6b6a63"  # 나머지 항목
TRACK = "#2a2a28"  # 아직 등장하지 않은 자리
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"


def render_reveal_frames(
    series: list[DataPoint],
    unit: str,
    title: str,
    out_dir: Path,
    width_px: int,
    height_px: int,
    highlight_index: int = 0,
) -> list[Path]:
    """항목이 하나씩 드러나는 프레임들을 만들고 경로를 순서대로 돌려준다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        _render_frame(
            series, unit, title, out_dir / f"chart_{revealed:02d}.png",
            width_px, height_px, highlight_index, revealed,
        )
        for revealed in range(1, len(series) + 1)
    ]


def _render_frame(
    series: list[DataPoint],
    unit: str,
    title: str,
    out_path: Path,
    width_px: int,
    height_px: int,
    highlight_index: int,
    revealed: int,
) -> Path:
    font_path = ensure_korean_font()
    font = fm.FontProperties(fname=str(font_path))

    dpi = 100
    figure, axes = plt.subplots(
        figsize=(width_px / dpi, height_px / dpi), dpi=dpi
    )
    figure.patch.set_alpha(0.0)

    # 사진 위에 얹히므로 차트 뒤에 어두운 판을 깔아 글자 대비를 확보한다.
    # savefig(transparent=True) 는 축 배경(axes.patch)까지 투명하게 만들기 때문에,
    # 판은 축 배경이 아니라 별도 도형으로 그려야 살아남는다.
    axes.add_patch(
        FancyBboxPatch(
            (0, 0),
            1,
            1,
            transform=axes.transAxes,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            facecolor=SURFACE,
            edgecolor="none",
            alpha=0.93,
            zorder=-10,
            clip_on=False,
        )
    )

    max_value = max(point.value for point in series) or 1.0
    bar_height = 0.58
    positions = list(range(len(series)))

    for index, point in enumerate(series):
        y = len(series) - 1 - index
        _rounded_bar(axes, 0, y, max_value * 1.0, bar_height, TRACK, alpha=0.55)

        if index < revealed:
            color = ACCENT if index == highlight_index else MUTED
            _rounded_bar(axes, 0, y, point.value, bar_height, color, alpha=1.0)
            axes.text(
                max_value * 0.012,
                y + bar_height * 0.72,
                point.label,
                va="bottom",
                ha="left",
                color=TEXT_PRIMARY,
                fontproperties=font,
                fontsize=24,
            )
            axes.text(
                point.value + max_value * 0.015,
                y,
                f"{_format_value(point.value)}{unit}",
                va="center",
                ha="left",
                color=TEXT_PRIMARY if index == highlight_index else TEXT_SECONDARY,
                fontproperties=font,
                fontsize=27,
                fontweight="bold",
            )

    # 제목도 판 안에 둔다. 판 밖으로 나가면 사진 위에 맨몸으로 얹혀 읽히지 않는다.
    axes.text(
        0.012,
        0.975,
        title,
        transform=axes.transAxes,
        va="top",
        ha="left",
        color=TEXT_SECONDARY,
        fontproperties=font,
        fontsize=25,
    )
    axes.set_xlim(0, max_value * 1.28)
    axes.set_ylim(-0.6, len(positions) + 0.35)
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)

    figure.tight_layout(pad=1.2)
    figure.savefig(out_path, transparent=True, dpi=dpi)
    plt.close(figure)
    return out_path


def _rounded_bar(axes, x: float, y: float, width: float, height: float, color: str, alpha: float):
    """값 쪽 끝이 둥근 막대. 값이 0 이어도 도형이 뒤집히지 않게 최소 폭을 준다."""
    rounding = height * 0.22
    patch = FancyBboxPatch(
        (x, y - height / 2),
        max(width - rounding, rounding),
        height,
        boxstyle=f"round,pad=0,rounding_size={rounding}",
        linewidth=0,
        facecolor=color,
        alpha=alpha,
        mutation_aspect=1,
    )
    axes.add_patch(patch)
    return patch


def _format_value(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.1f}"
