"""基盤モデル呼び出しの差し替え可能な注入点。

設計書の非機能要件「差し替え可能性: 基盤モデル(計画側)はインターフェース越しに
差し替え可能とする。特定ベンダー・特定モデル名をループ本体のロジックに直接埋め込まない」
を満たすため、functions.py の9関数はここで注入された FoundationModelClient のみを経由して
モデルを呼び出す。呼び出し側(MainLoop・SkillRunner・Watchdog)のコードは、
configure_model_client() で実装を差し替えても一切変更不要。
"""
from __future__ import annotations

from typing import Any, Protocol


class FoundationModelClient(Protocol):
    """基盤モデル呼び出しの抽象インターフェース。

    実装例: Anthropic Claude / OpenAI GPT-4V などのVLM APIをラップしたクライアントを
    このProtocolに準拠させ、configure_model_client() に渡す。
    """

    def complete(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        images: list[bytes] | None,
        response_model: Any,
    ) -> Any:
        """task(=9関数のいずれかの名前)に応じたプロンプトを組み立ててモデルを呼び出し、
        response_model に準拠した値を返す。"""
        ...


class ScriptedFoundationModelClient:
    """テスト・決定的リプレイ用のクライアント。task名ごとに事前登録した応答を順番に返す。

    実運用のモデル呼び出しは行わない。フィクスチャで期待するアクション列を
    そのまま注入できるため、Final Phaseの決定的フェイクゲームテストにも使う。
    """

    def __init__(self, responses: dict[str, list[Any]] | None = None):
        self._responses: dict[str, list[Any]] = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[dict[str, Any]] = []

    def queue(self, task: str, response: Any) -> None:
        self._responses.setdefault(task, []).append(response)

    def complete(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        images: list[bytes] | None,
        response_model: Any,
    ) -> Any:
        self.calls.append({"task": task, "payload": payload, "images_count": len(images or [])})
        queue = self._responses.get(task)
        if not queue:
            raise AssertionError(
                f"ScriptedFoundationModelClient: task='{task}' の応答が登録されていません。"
                " queue()/responses で事前に登録してください。"
            )
        return queue.pop(0)


_active_client: FoundationModelClient | None = None


def configure_model_client(client: FoundationModelClient) -> None:
    """基盤モデルクライアントの実装をグローバルに差し替える。"""
    global _active_client
    _active_client = client


def get_model_client() -> FoundationModelClient:
    if _active_client is None:
        raise RuntimeError(
            "FoundationModelClientが未設定です。configure_model_client() で"
            "実装(本番用クライアントまたはScriptedFoundationModelClient)を設定してください。"
        )
    return _active_client


def reset_model_client() -> None:
    """主にテストの後片付け用。"""
    global _active_client
    _active_client = None
