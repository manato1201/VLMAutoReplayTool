"""GUIの動き(style.css)の検査: reduced-motion 対応と、無限ループの絞り込み。"""
from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "src/vlm_auto_replay/gui/static/style.css").read_text(encoding="utf-8")


def _rules(css: str):
    """(セレクタ, 本体) の単純な列挙(@media の中も含めて平坦に)。"""
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def test_reduced_motion_disables_animation_and_transition():
    m = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*)\}\s*$", CSS, re.DOTALL)
    assert m, "prefers-reduced-motion のブロックが無い"
    body = m.group(1)
    assert "animation: none !important" in body and "transition: none !important" in body
    assert "*::before" in body and "*::after" in body


def test_infinite_animation_only_while_running():
    infinite = [(sel, b) for sel, b in _rules(CSS) if "infinite" in b]
    assert len(infinite) == 1
    sel, _ = infinite[0]
    assert "body.is-running" in sel and ".todo-card.focused" in sel


def test_one_shot_state_animation_is_short_and_runs_once():
    m = re.search(r"\.eyebrow\.state-change\s*\{[^}]*animation:\s*state-pop (\d+)ms[^;]* 1;", CSS)
    assert m and 150 <= int(m.group(1)) <= 300


def test_keyframes_only_use_transform_and_opacity():
    for m in re.finditer(r"@keyframes ([\w-]+)\s*\{(.*?)\n\}", CSS, re.DOTALL):
        props = set(re.findall(r"([a-z-]+)\s*:", m.group(2)))
        assert props <= {"transform", "opacity"}, (m.group(1), props)


def test_new_one_shot_animations_run_once_and_are_short():
    # エラーの横揺れ・新しい行の入場: どれも 1 回きり・150〜300ms(無限ループは hud-pulse だけ)
    for sel, name in ((r"\.error-text\.shake", "error-shake"), (r"\.is-new", "item-enter")):
        m = re.search(sel + r"\s*\{[^}]*animation:\s*" + name + r" (\d+)ms[^;]* 1\b", CSS)
        assert m and 150 <= int(m.group(1)) <= 300, name
