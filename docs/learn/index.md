# VLMAutoReplayTool 学習資料(保守・拡張の実務編)

このフォルダは、**このツールを直す・足す人**のための資料です。基礎から順に学ぶ講義は [../lecture/index.md](../lecture/index.md)(第 0〜5 回)。各 Phase の図・技術解説は [../TECHNICAL_OVERVIEW.md](../TECHNICAL_OVERVIEW.md)(Mermaid の図つき)にあります。

| # | 資料 | いつ読むか |
|---|---|---|
| 1 | [全体像](01-overview.md) | 何がどう動いているか・Phase の対応・状態の置き場所・環境変数 |
| 2 | [コードの読み方](02-code-tour.md) | どのファイルから読むか |
| 3 | [内側のしくみ](03-internals.md) | 操作 GUI(実行の流れ・HUD)・保存・認証と管理画面の分離 |
| 4 | [拡張のレシピ](04-extend.md) | 入力・スキル・モデル・Watchdog の規則・RAG を**足したい** |
| 5 | [テストの書き方と実行](05-testing.md) | テストを回す・偽のモデルと偽のゲーム・文書の検査 |
| 6 | [トラブルと保守](06-troubleshooting-maintenance.md) | 403・「GUI で実機が動かない」・既知の問題・定期の保守 |
| – | [用語集](glossary.md) | 分からない言葉が出た |

## 最初の 15 分

1. [README](../../README.md) の手順で `pip install -e ".[dev,gui]"` → `pytest -q` → `python -m vlm_auto_replay.gui`。
2. ブラウザで `http://127.0.0.1:8765` を開き、**ゴール分解 → 実行開始**(デモのゲームが動く)。[構成図](../diagrams/architecture.svg)と見比べる。
3. 講義[第 2 回](../lecture/02-main-loop.md)の「理由が空なら実行しない」の例を、`tests/test_main_loop.py` と並べて読む。

## 目的別の近道

| やりたいこと | 読む場所 |
|---|---|
| 新しい入力(スティックなど)を足す | [4 拡張](04-extend.md) レシピ 1・講義[第 5 回](../lecture/05-skills-testing-capstone.md) |
| 本物の VLM に差し替える | [4 拡張](04-extend.md) レシピ 2・講義[第 0 回](../lecture/00-introduction.md) |
| サンドボックスを強くする | [4 拡張](04-extend.md) レシピ 4・講義[第 4 回](../lecture/04-actions-and-sandbox.md) |
| 環境変数・認証・動作確認の詳細 | [旧 README の詳細](../reference/setup-and-operations.md) |
| 設計の経緯 | [VLMAutoReplayTool_DESIGN.md](../../VLMAutoReplayTool_DESIGN.md) |
