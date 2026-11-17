"""OpenAI 互換(`/v1/chat/completions`)の FoundationModelClient 実装。

Ollama(`http://127.0.0.1:11434/v1`)・llama.cpp の server・LM Studio など、ローカルの
OpenAI 互換サーバーにつなぐための薄いクライアント。画像は OpenAI の ``image_url``
(data URI)形式で送る。

安全のための決めごと:
- 接続先は **loopback(127.0.0.1 / localhost / ::1)の http**、または **https** だけ。
  それ以外の http(平文で外へ出る)・URL に埋め込んだ認証情報・http(s) 以外は拒否する。
- API キーは**環境変数の名前**だけを受け取り、値はヘッダーにだけ使う。
  エラー文・repr にキーの値を出さない(応答本文に混じっていても伏せる)。
- リダイレクトは追わない(Authorization ヘッダーを別の場所へ送らないため)。

実モデルでの精度・速度・VRAM は**未測定**(このモジュールのテストは偽サーバーで行う)。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests
from pydantic import BaseModel, TypeAdapter, ValidationError

_LOOPBACK_NAMES = {"localhost"}
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}

_TASK_INSTRUCTIONS: dict[str, str] = {
    "decompose_goal_to_todo": "達成するために必要な作業を TODO リストに分解してください。",
    "explain_action_choice": "なぜそのアクションにしたのかを、1〜3文で簡潔に説明してください。",
    "generate_next_action": "Goal を達成するための次のアクションを1つ生成してください(画面の観測画像つき)。",
    "extract_experience": "再利用可能な高価値の知見(落とし穴・操作ルール・前提条件)を抽出してください。",
    "summarize_screen_change": "2枚の画像(前・後)で観測できる変化だけを1〜2文で要約してください。",
    "merge_duplicate_operations": "重複する操作をまとめ、差分を Parameter にして1つに統合してください。",
    "generate_code_from_video_and_procedure": "動画の参照と手順書から Python コードを生成してください。",
    "extract_reusable_subroutine": "再利用可能なサブルーチンとして切り出してください。",
    "diagnose_stall": "処理が停滞している原因を分析し、次に試すべきアプローチを特定してください。",
}


class ModelClientError(RuntimeError):
    """モデル呼び出しの失敗(メッセージに API キーの値は含まれない)。"""


class InvalidEndpointError(ModelClientError, ValueError):
    """許可されない接続先(URL)。"""


def validate_base_url(base_url: str) -> str:
    """接続先を検査し、末尾の `/` を除いた URL を返す。許可外なら InvalidEndpointError。"""
    if not isinstance(base_url, str) or not base_url.strip():
        raise InvalidEndpointError("base_url が空です。")
    try:
        parts = urlsplit(base_url.strip())
        host = parts.hostname
        parts.port  # noqa: B018  不正なポートはここで ValueError
    except ValueError as exc:
        raise InvalidEndpointError(f"base_url を解釈できません: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidEndpointError("base_url は http:// か https:// で始めてください。")
    if not host:
        raise InvalidEndpointError("base_url にホスト名がありません。")
    if parts.username is not None or parts.password is not None:
        raise InvalidEndpointError("base_url に認証情報を埋め込まないでください(キーは環境変数で渡す)。")
    if parts.scheme == "http" and not _is_loopback(host):
        raise InvalidEndpointError(
            "平文の http は 127.0.0.1 / localhost のみ許可します。外部のサーバーは https を使ってください。"
        )
    return base_url.strip().rstrip("/")


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def sniff_image_mime(data: bytes) -> str:
    """先頭バイトから画像の MIME を判定する。判定できなければ image/png。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def image_data_uri(data: bytes) -> str:
    return f"data:{sniff_image_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"


class OpenAICompatClient:
    """`FoundationModelClient` を満たす、OpenAI 互換サーバー向けクライアント。

    Args:
        base_url: 例 ``http://127.0.0.1:11434/v1``(Ollama)。末尾に ``/chat/completions`` を足して呼ぶ。
        model: サーバー側のモデル名(例 Ollama なら ``llava``)。
        api_key_env: API キーを入れた**環境変数の名前**(任意)。値は引数で受けない。
        timeout_sec: 1 回の応答待ちの上限(秒)。ローカルの VLM は遅いので既定は長め。
        max_retries: 接続失敗・タイムアウト・429/5xx のときの再試行回数(最初の 1 回は含まない)。
        response_format: ``"json_object"``(既定)または ``"none"``(非対応のサーバー向け)。
        environ: 環境変数の参照元(テスト用)。
        sleep: 待機関数(テスト用)。
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str | None = None,
        timeout_sec: float = 120.0,
        max_retries: int = 2,
        backoff_sec: float = 0.5,
        response_format: str = "json_object",
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not model:
            raise ValueError("model が空です。")
        if response_format not in ("json_object", "none"):
            raise ValueError("response_format は 'json_object' か 'none' です。")
        if timeout_sec <= 0 or max_retries < 0:
            raise ValueError("timeout_sec は正、max_retries は 0 以上にしてください。")
        self._base_url = validate_base_url(base_url)
        self._model = model
        self._api_key_env = api_key_env or None
        self._timeout = timeout_sec
        self._max_retries = max_retries
        self._backoff = backoff_sec
        self._response_format = response_format
        self._environ = environ if environ is not None else os.environ
        self._sleep = sleep
        self._loopback = _is_loopback(urlsplit(self._base_url).hostname or "")

    def __repr__(self) -> str:
        return (
            f"OpenAICompatClient(base_url={self._base_url!r}, model={self._model!r}, "
            f"api_key_env={self._api_key_env!r})"
        )

    # --- FoundationModelClient ---
    def complete(
        self,
        *,
        task: str,
        payload: dict[str, Any],
        images: list[bytes] | None,
        response_model: Any,
    ) -> Any:
        adapter = TypeAdapter(response_model)
        wrap_items = not (isinstance(response_model, type) and issubclass(response_model, BaseModel))
        schema = adapter.json_schema()
        if wrap_items:
            schema = {"type": "object", "properties": {"items": schema}, "required": ["items"]}
        instruction = _TASK_INSTRUCTIONS.get(task, f"タスク '{task}' を実行してください。")
        system = (
            f"{instruction}\n"
            "出力は次の JSON Schema に従う JSON オブジェクト1つだけ(前置き・説明・コードフェンスなし)。\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        text = self.chat(
            json.dumps({"task": task, "payload": payload}, ensure_ascii=False, default=str),
            images=images,
            system=system,
            json_mode=self._response_format == "json_object",
        )
        data = _extract_json(text)
        if wrap_items and isinstance(data, dict) and "items" in data:
            data = data["items"]
        try:
            return adapter.validate_python(data)
        except ValidationError as exc:
            raise ModelClientError(
                f"task='{task}': モデルの出力がスキーマに合いません({len(exc.errors())} 件の不一致)。"
            ) from exc

    # --- 単独で使える下位 API(ask CLI など) ---
    def chat(
        self,
        prompt: str,
        *,
        images: list[bytes] | None = None,
        system: str | None = None,
        json_mode: bool = False,
    ) -> str:
        """1 回の chat/completions を呼び、応答の文字列を返す。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images or []:
            content.append({"type": "image_url", "image_url": {"url": image_data_uri(img)}})
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        body: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": 0, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = self._post(body)
        try:
            message = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError("応答に choices[0].message.content がありません。") from exc
        if isinstance(message, list):  # 一部のサーバーはパーツ配列で返す
            message = "".join(p.get("text", "") for p in message if isinstance(p, dict))
        if not isinstance(message, str):
            raise ModelClientError("応答の content が文字列ではありません。")
        return message

    # --- 内部 ---
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _api_key(self) -> str | None:
        if not self._api_key_env:
            return None
        key = self._environ.get(self._api_key_env)
        if not key:
            raise ModelClientError(f"環境変数 {self._api_key_env} が設定されていません(API キー)。")
        return key

    def _redact(self, text: str) -> str:
        key = self._environ.get(self._api_key_env or "") if self._api_key_env else None
        if key:
            text = text.replace(key, "***")
        return text

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = self._headers()
        last: str = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                self._sleep(self._backoff * (2 ** (attempt - 1)))
            try:
                with requests.Session() as session:
                    if self._loopback:
                        session.trust_env = False  # ローカル宛てを環境のプロキシへ回さない
                    resp = session.post(
                        url, json=body, headers=headers, timeout=self._timeout, allow_redirects=False
                    )
            except requests.Timeout:
                last = f"タイムアウト({self._timeout:g} 秒)"
                continue
            except requests.ConnectionError:
                last = f"接続できません({self._base_url})。サーバーは起動していますか"
                continue
            except requests.RequestException as exc:
                raise ModelClientError(f"通信に失敗しました: {self._redact(type(exc).__name__)}") from None
            if resp.status_code in _RETRY_STATUS:
                last = f"HTTP {resp.status_code}"
                continue
            if resp.is_redirect or 300 <= resp.status_code < 400:
                raise ModelClientError(f"リダイレクト(HTTP {resp.status_code})は追いません。base_url を確認して。")
            if resp.status_code >= 400:
                detail = self._redact(resp.text[:200]).replace("\n", " ")
                raise ModelClientError(f"HTTP {resp.status_code}: {detail}")
            try:
                data = resp.json()
            except ValueError:
                raise ModelClientError("応答が JSON ではありません。") from None
            if not isinstance(data, dict):
                raise ModelClientError("応答の形式が想定と違います。")
            return data
        raise ModelClientError(f"{last}。{self._max_retries + 1} 回試して失敗しました。")


_FENCE = re.compile(r"^```[a-zA-Z0-9]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _extract_json(text: str) -> Any:
    """応答からJSONを取り出す(コードフェンス・前後の文章を許容)。"""
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(s[i:])
                return obj
            except ValueError:
                continue
    raise ModelClientError("モデルの出力から JSON を取り出せませんでした。")


def client_from_env(environ: Mapping[str, str] | None = None) -> OpenAICompatClient | None:
    """環境変数から実クライアントを作る。`VLM_MODEL_BACKEND` が openai-compat 以外なら None(=デモ)。

    VLM_MODEL_BACKEND   demo(既定) | openai-compat
    VLM_LLM_BASE_URL    例 http://127.0.0.1:11434/v1
    VLM_LLM_MODEL       サーバー側のモデル名
    VLM_LLM_API_KEY_ENV キーを入れた環境変数の名前(任意。値そのものは置かない)
    VLM_LLM_TIMEOUT_SEC / VLM_LLM_MAX_RETRIES / VLM_LLM_RESPONSE_FORMAT(json_object|none)
    """
    env = environ if environ is not None else os.environ
    backend = env.get("VLM_MODEL_BACKEND", "demo").strip().lower()
    if backend in ("", "demo"):
        return None
    if backend != "openai-compat":
        raise ModelClientError(f"VLM_MODEL_BACKEND が不正です: {backend!r}(demo か openai-compat)")
    base_url = env.get("VLM_LLM_BASE_URL", "")
    model = env.get("VLM_LLM_MODEL", "")
    if not base_url or not model:
        raise ModelClientError("openai-compat には VLM_LLM_BASE_URL と VLM_LLM_MODEL が必要です。")
    try:
        return OpenAICompatClient(
            base_url=base_url,
            model=model,
            api_key_env=env.get("VLM_LLM_API_KEY_ENV") or None,
            timeout_sec=float(env.get("VLM_LLM_TIMEOUT_SEC", "120")),
            max_retries=int(env.get("VLM_LLM_MAX_RETRIES", "2")),
            response_format=env.get("VLM_LLM_RESPONSE_FORMAT", "json_object"),
            environ=env,
        )
    except ValueError as exc:
        if isinstance(exc, InvalidEndpointError):
            raise
        raise ModelClientError(f"VLM_LLM_* の設定が不正です: {exc}") from exc
