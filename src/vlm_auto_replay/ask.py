"""OpenAI 互換サーバーに、1 回だけ質問する最小の入口(別リポジトリからの呼び出し用)。

    python -m vlm_auto_replay.ask --image x.png "この画面の状態を説明して"

接続先・モデルは引数か環境変数(VLM_LLM_BASE_URL / VLM_LLM_MODEL / VLM_LLM_API_KEY_ENV)。
応答の文字列を標準出力へ出す。ライブラリとしては ``ask()`` を呼ぶ。
実モデルでの精度・速度は未測定。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .prompts.openai_compat_client import ModelClientError, OpenAICompatClient


def ask(
    prompt: str,
    *,
    image_paths: list[str | Path] | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    timeout_sec: float | None = None,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """画像(任意)つきで 1 回質問し、応答の文字列を返す。未指定の項目は環境変数から。"""
    env = environ if environ is not None else os.environ
    base_url = base_url or env.get("VLM_LLM_BASE_URL")
    model = model or env.get("VLM_LLM_MODEL")
    if not base_url or not model:
        raise ModelClientError("base_url と model が必要です(引数か VLM_LLM_BASE_URL / VLM_LLM_MODEL)。")
    client = OpenAICompatClient(
        base_url=base_url,
        model=model,
        api_key_env=api_key_env or env.get("VLM_LLM_API_KEY_ENV") or None,
        timeout_sec=timeout_sec or float(env.get("VLM_LLM_TIMEOUT_SEC", "120")),
        environ=env,
    )
    images = [Path(p).read_bytes() for p in image_paths or []]
    return client.chat(prompt, images=images or None, system=system)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m vlm_auto_replay.ask", description=__doc__.splitlines()[0])
    p.add_argument("prompt", help="質問文")
    p.add_argument("--image", action="append", default=[], metavar="PATH", help="画像(複数回指定可)")
    p.add_argument("--base-url", help="例 http://127.0.0.1:11434/v1(未指定は VLM_LLM_BASE_URL)")
    p.add_argument("--model", help="サーバー側のモデル名(未指定は VLM_LLM_MODEL)")
    p.add_argument("--api-key-env", help="API キーを入れた環境変数の名前(値ではない)")
    p.add_argument("--timeout", type=float, help="応答待ちの上限(秒)")
    p.add_argument("--system", help="システムプロンプト")
    args = p.parse_args(argv)
    try:
        print(
            ask(
                args.prompt,
                image_paths=args.image,
                base_url=args.base_url,
                model=args.model,
                api_key_env=args.api_key_env,
                timeout_sec=args.timeout,
                system=args.system,
            )
        )
    except (ModelClientError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
