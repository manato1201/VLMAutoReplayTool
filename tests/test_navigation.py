"""Phase7/Final Phase: Localizerの4段パイプラインと偽陽性防御の検証。"""
from __future__ import annotations

import pytest
from vlm_auto_replay.actions.api_primitives import ApiPrimitives, NullKeyboardMouseBackend, NullPadBackend, ScriptedOcrBackend
from vlm_auto_replay.navigation.localizer import Localizer, SimpleHashDescriptorExtractor
from vlm_auto_replay.navigation.screen_state import ScreenState, UnknownTransitionError, validate_transition

_IMAGES = {
    "menu": b"MENU_SCREEN_BYTES",
    "battle": b"BATTLE_SCREEN_BYTES",
}


def _make_localizer(ocr_text: str) -> Localizer:
    api = ApiPrimitives(NullPadBackend(), NullKeyboardMouseBackend(), ScriptedOcrBackend(default_text=ocr_text))
    return Localizer(
        descriptor_extractor=SimpleHashDescriptorExtractor(),
        image_loader=lambda ref: _IMAGES[ref],
        api=api,
    )


def _states() -> list[ScreenState]:
    return [
        ScreenState(stateId="menu", referenceImageRef="menu", ocrLandmarks=["MENU"], knownTransitions=["battle"]),
        ScreenState(stateId="battle", referenceImageRef="battle", ocrLandmarks=["HP", "MP"], knownTransitions=["menu"]),
    ]


def test_four_stage_pipeline_are_independent_methods():
    localizer = _make_localizer(ocr_text="MENU")
    assert hasattr(localizer, "_global_descriptor_search")
    assert hasattr(localizer, "_rerank")
    assert hasattr(localizer, "_final_confirm")


def test_localize_picks_best_matching_state_and_confirms():
    localizer = _make_localizer(ocr_text="MENU here")
    result = localizer.localize(_IMAGES["menu"], _states())
    assert result is not None
    assert result.stateId == "menu"


def test_final_confirmation_rejects_plausible_but_wrong_candidate():
    """coarse/scoringが誤って選んだ候補を、OCRランドマーク不一致で最終的に棄却すること(偽陽性防御)。"""
    localizer = _make_localizer(ocr_text="totally unrelated text")
    result = localizer.localize(_IMAGES["menu"], _states())
    assert result is None


def test_coarse_search_limits_candidates_to_top_k():
    localizer = _make_localizer(ocr_text="MENU")
    many_states = _states() * 5  # 10状態
    coarse = localizer._global_descriptor_search(_IMAGES["menu"], many_states)
    assert len(coarse) <= localizer._coarse_top_k


def test_validate_transition_detects_unknown_transition():
    menu, battle = _states()
    validate_transition(menu, battle)  # 定義済みなので例外なし
    with pytest.raises(UnknownTransitionError):
        validate_transition(battle, ScreenState(stateId="shop", referenceImageRef="x", ocrLandmarks=[], knownTransitions=[]))
