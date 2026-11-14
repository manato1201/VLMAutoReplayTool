# VLMAutoReplayTool 講義(基礎から)

ゲーム自動プレイエージェントの**考え方・コード・アルゴリズム・専門用語**を、順に学ぶ講義です。各回は「**ゴール → 説明と実行できる例 → 確認問題 → 解答 → まとめ**」で、コードは実際に動かして確かめてあります(`tests/test_docs.py` が、ずれと出力を検査します)。

| 回 | 題 | 学ぶこと | 目安 |
|---|---|---|---|
| [0](00-introduction.md) | 計画と実行を分ける | 設計の軸・9 つの意図・**`Protocol` で差し替える**偽のモデル | 1.5 h |
| [1](01-prompts-and-types.md) | 型つきのプロンプト層 | **Pydantic の検証**・`Literal`・`model_validator`・**RAG の境界**(`ast` で守る) | 2.5 h |
| [2](02-main-loop.md) | メインループ | 1 ステップの流れ・**変化を見て進む**・**理由が空なら実行しない**・部品の注入 | 3 h |
| [3](03-watchdog.md) | Watchdog | 停滞・**診断と強制の OR**・再構築の入力を絞る | 2.5 h |
| [4](04-actions-and-sandbox.md) | 実行の層とサンドボックス | **許可リスト**・`exec` と制限した `__builtins__`・**ブロックリストの限界** | 3.5 h |
| [5](05-skills-testing-capstone.md) | スキル・テスト・総合演習 | 自動抽出・**ホールドアウトのゲート**・RAG の TODO 生成・新しい入力を足す | 半日 |

## 進め方

- **前提**: Python の基本。Pydantic・`Protocol`・`ast` は、その回で必要な分を説明します。
- 例は、リポジトリのルートで動かします(`src/` を import できるよう、`tools/docs_pythonpath.txt` で設定済み)。**本物のモデル・ゲーム・ドライバは使いません**。
- 詰まったら [用語集](../learn/glossary.md)。講義のあとは [学習資料](../learn/index.md)。
