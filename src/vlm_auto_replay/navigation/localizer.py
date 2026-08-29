"""Phase7: Localizer。coarse search→scoring→localization→final confirmationの4段パイプライン。

各段は独立した関数(メソッド)として分離する。coarse search / scoring の実装は
`DescriptorExtractor` を差し替えることで軽量ハッシュ比較から本格的なCNN埋め込み /
局所特徴マッチングへ後続反復で置き換え可能(優先度注記: Phase7は複数回反復前提)。

final confirmationはPhase3のApiPrimitives.ocr()を再利用し、OCRランドマーク照合による
独立検証を行う。「もっともらしいが誤った」候補をここで棄却する安全ゲート。
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from typing import Protocol

from ..actions.api_primitives import ApiPrimitives
from .screen_state import ScreenState

ImageLoader = Callable[[str], bytes]


class DescriptorExtractor(Protocol):
    def extract(self, image: bytes) -> tuple[float, ...]: ...


class SimpleHashDescriptorExtractor:
    """バイト列から決定的な特徴量ベクトルを作る軽量実装(global descriptorの代替)。

    TODO(Phase7反復): 実運用では画像検索の粗段として一般的なCNN埋め込み
    (例: perceptual hash, CLIP embedding)に差し替えることを想定する。
    """

    def extract(self, image: bytes) -> tuple[float, ...]:
        digest = hashlib.sha256(image).digest()
        return tuple(b / 255.0 for b in digest[:16])


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class Localizer:
    def __init__(
        self,
        descriptor_extractor: DescriptorExtractor,
        image_loader: ImageLoader,
        api: ApiPrimitives,
        confirm_region: tuple[int, int, int, int] = (0, 0, 0, 0),
        coarse_top_k: int = 5,
    ):
        self._extractor = descriptor_extractor
        self._load_image = image_loader
        self._api = api
        self._confirm_region = confirm_region
        self._coarse_top_k = coarse_top_k
        # referenceImageRef -> 特徴量のキャッシュ。既知状態の集合は静的な参照画像なので、
        # localize()を繰り返し呼んでも同じ状態の特徴量を毎回再計算しない
        # (本格的なCNN埋め込み等、抽出コストが高い実装に差し替えたときほど効いてくる)。
        self._descriptor_cache: dict[str, tuple[float, ...]] = {}

    def _descriptor_for(self, state: ScreenState) -> tuple[float, ...]:
        cached = self._descriptor_cache.get(state.referenceImageRef)
        if cached is None:
            cached = self._extractor.extract(self._load_image(state.referenceImageRef))
            self._descriptor_cache[state.referenceImageRef] = cached
        return cached

    def localize(self, observation: bytes, known_states: list[ScreenState]) -> ScreenState | None:
        coarse_candidates = self._global_descriptor_search(observation, known_states)  # 1. coarse search
        scored = self._rerank(observation, coarse_candidates)  # 2. scoring
        best = scored[0][0] if scored else None  # 3. localization
        if best is None:
            return None
        if not self._final_confirm(best):  # 4. final confirmation
            return None  # もっともらしいが誤った候補を棄却
        return best

    def _global_descriptor_search(
        self, observation: bytes, known_states: list[ScreenState]
    ) -> list[ScreenState]:
        """1. coarse search: global descriptor近傍検索による候補絞り込み。

        candidate数をcoarse_top_kで打ち切ることで、scoring段の計算量を
        全状態総当たりではなく線形の定数倍に抑える。
        """
        if not known_states:
            return []
        obs_vec = self._extractor.extract(observation)
        scored = sorted(
            known_states,
            key=lambda s: -_cosine_similarity(obs_vec, self._descriptor_for(s)),
        )
        return scored[: self._coarse_top_k]

    def _rerank(
        self, observation: bytes, candidates: list[ScreenState]
    ) -> list[tuple[ScreenState, float]]:
        """2. scoring: re-ranking段。局所特徴マッチング/テンプレートマッチの代わりに
        同じdescriptor類似度で再計算する軽量実装(TODO: 本格的なre-rankerに置換)。
        """
        obs_vec = self._extractor.extract(observation)
        scored = [(state, _cosine_similarity(obs_vec, self._descriptor_for(state))) for state in candidates]
        return sorted(scored, key=lambda pair: -pair[1])

    def _final_confirm(self, candidate: ScreenState) -> bool:
        """4. final confirmation: OCRランドマークによる独立検証(偽陽性防御)。"""
        if not candidate.ocrLandmarks:
            # ランドマーク未定義のStateは確認をスキップする(将来的に必須化を検討)。
            return True
        observed_text = self._api.ocr(self._confirm_region)
        return any(landmark in observed_text for landmark in candidate.ocrLandmarks)
