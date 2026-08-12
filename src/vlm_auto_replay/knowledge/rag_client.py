"""Phase4: RAGクライアント。

DevelopmentRAGEnvironment の rag_local_bridge.py (既定ポート:8766, X-API-Keyヘッダ認証) を
そのまま再利用する。新規RAGサービスは構築しない。docs/experienceは同一ブリッジ上の
別namespaceとして実装する。

アーキテクチャ境界: このモジュールは prompts.functions.generate_next_action からは
一切importされない(1ステップ実行中のRAG呼び出しを0回に保つため)。RAG呼び出しは
TODO生成の前段(このモジュールと knowledge/experience.py)にのみ存在する。
"""

from __future__ import annotations

from typing import Literal

import requests


class VLMReplayRagClient:
    BASE_URL = "http://localhost:8766"

    def __init__(self, api_key: str, base_url: str | None = None):
        self._headers = {"X-API-Key": api_key}
        self._base_url = base_url or self.BASE_URL

    def search(
        self, query: str, namespace: Literal["docs", "experience"], limit: int = 6
    ) -> dict:
        resp = requests.post(
            f"{self._base_url}/search",
            headers=self._headers,
            json={"query": query, "limit": limit, "namespaces": [namespace]},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
