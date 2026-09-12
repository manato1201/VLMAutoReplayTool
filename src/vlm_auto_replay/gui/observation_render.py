"""StepLog.observationRef のサムネイル用SVGレンダリング(`GET /api/observation/{ref}`)。

`DemoGame`の観測形式(`progress=X/Y`というテキスト)を前提にした合成描画。実運用で
ScreenCaptureを実キャプチャに差し替えた場合、observationRefは実際のスクリーンショット
バイト列/URLを指すため、この合成描画は経由しない(エンドポイントがバイト列を
そのまま返すだけでよくなる)。
"""
from __future__ import annotations

import re

_DEMO_OBSERVATION_RE = re.compile(r"progress=(\d+)/(\d+)")


def render_observation_svg(observation: bytes) -> bytes:
    """観測バイト列(デモ形式)をStepLogサムネイル用のSVGにレンダリングする。"""
    match = _DEMO_OBSERVATION_RE.fullmatch(observation.decode(errors="ignore"))
    if match is None:
        return _render_placeholder_svg(observation)
    return _render_progress_svg(int(match.group(1)), int(match.group(2)))


def _render_progress_svg(progress: int, steps_to_win: int) -> bytes:
    width, height = 240, 135
    ratio = (progress / steps_to_win) if steps_to_win else 0.0
    bar_width = round(200 * min(max(ratio, 0.0), 1.0))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#150f23"/>'
        f'<text x="16" y="32" fill="#ffffff" font-family="Rubik, sans-serif" font-size="14" '
        f'font-weight="600">DEMO SCREEN</text>'
        f'<rect x="16" y="60" width="200" height="16" rx="8" fill="#362d59"/>'
        f'<rect x="16" y="60" width="{bar_width}" height="16" rx="8" fill="#c2ef4e"/>'
        f'<text x="16" y="100" fill="#bdb8c0" font-family="Rubik, sans-serif" font-size="12">'
        f"progress {progress} / {steps_to_win}</text>"
        f"</svg>"
    ).encode()


def _render_placeholder_svg(observation: bytes) -> bytes:
    text = observation.decode(errors="ignore")[:40]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135" viewBox="0 0 240 135">'
        '<rect width="240" height="135" fill="#150f23"/>'
        '<text x="16" y="32" fill="#ffffff" font-family="Rubik, sans-serif" font-size="14" '
        'font-weight="600">OBSERVATION</text>'
        f'<text x="16" y="70" fill="#bdb8c0" font-family="Monaco, monospace" font-size="11">{text}</text>'
        "</svg>"
    ).encode()
