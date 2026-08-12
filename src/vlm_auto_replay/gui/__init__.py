"""VLMAutoReplayToolを操作するためのローカルWeb GUI(FastAPI+静的フロントエンド)。

このGUIはPhase1-5のコアエンジンをその場で動かして確認できるように、実VLM/実HIDの
代わりに決定的な `DemoModelClient` / `DemoGame` を既定で使用する(runtime.py参照)。
実運用のモデル・HIDバックエンドに差し替える場合は、サーバ起動前に
`prompts.model_client.configure_model_client()` を呼び出しておけばGUI側の変更は不要。
"""
