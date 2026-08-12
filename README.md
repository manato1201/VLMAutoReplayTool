# VLMAutoReplayTool

「基盤モデルによる計画」と「専用モデル・スキル・APIによる実行」を分離したゲーム自動プレイエージェントのコア骨格実装。設計は [`VLMAutoReplayTool_DESIGN.md`](VLMAutoReplayTool_DESIGN.md) を参照。`VLMAutoReplayTool_UI_DESIGN.md` はSentri風の汎用デザイントークン集で、本ツール固有の画面仕様は未定義のため、今回のスコープには含めていない。

## 実装範囲(今回のスコープ)

設計書のPhase0〜7のうち、**Phase1〜5をフル実装**し、実データフローが通る状態にした。Phase6/7(スキル自動抽出・視覚的自己位置推定)は設計書自身が「複数回反復が前提」と明記しているR&D領域のため、実データで動く軽量な実装+反復ポイントをTODOコメントで明示する形にとどめている。

| Phase | 内容 | 実装状態 |
|---|---|---|
| Phase1 | プロンプトテンプレート層(9関数) | `src/vlm_auto_replay/prompts/` フル実装 |
| Phase2 | メインループ+StepLog | `src/vlm_auto_replay/loop/main_loop.py` フル実装 |
| Phase3 | Action実行層(Skill/API+HID) | `src/vlm_auto_replay/actions/` フル実装(ViGEm/SendInputは実装済みだが実機ドライバ依存) |
| Phase4 | 知識ソース+RAG(TODO生成時限定) | `src/vlm_auto_replay/knowledge/` フル実装 |
| Phase5 | Watchdog+TODO再構築 | `src/vlm_auto_replay/loop/watchdog.py` フル実装 |
| Phase6 | スキル自動抽出 | `src/vlm_auto_replay/skills/` 軽量実装(TODOコメントあり) |
| Phase7 | ナビゲーション推論 | `src/vlm_auto_replay/navigation/` 軽量実装(TODOコメントあり) |

## セットアップ

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

Windows実機でのHID制御(ViGEm仮想パッド)を使う場合は追加で:

```bash
.venv/Scripts/pip install -e ".[hid]"
```

(別途 [ViGEmBus](https://github.com/ViGEm/ViGEmBus) ドライバのインストールが必要)

## テスト

```bash
.venv/Scripts/pytest -q
```

40件のテストで以下を検証している:

- Phase1: 9関数がすべて固定入出力スキーマを持ち、モデル実装(`FoundationModelClient`)を差し替えても呼び出し側コードが変更不要であること
- Phase2: 決定的フェイクゲーム(`tests/fixtures/fake_game.py`)を使い、`resultObservationSummary`による状態変化確認後にのみループが進むこと(固定ディレイに依存しない)
- Phase3: procedureスキルがMainLoopを再利用し(独立ループを持たない)、非強制的なガイダンスからの逸脱を許容すること。scriptスキルのサンドボックスがファイル/ネットワークアクセスを遮断すること
- Phase4: `generate_next_action`実行経路でRAGクライアントのimport・呼び出しが0回であることをAST静的解析とモックのコールカウントで実証
- Phase5: 一時的失敗では再構築が発火せず、閾値到達または`diagnose_stall`の判定のいずれかで再構築が発火する二重条件
- Phase6: 明らかな3ステップ反復フィクスチャから正確に1つのマージ済みSkillが生成されること
- Phase7: 「もっともらしいが誤った」候補をOCRランドマーク照合(final confirmation)で棄却できること(偽陽性防御)

## 操作用GUI

コアエンジンをブラウザから操作できるローカルWebダッシュボードを同梱している(`src/vlm_auto_replay/gui/`)。ゴール入力→TODO分解→実行開始→StepLogのライブ表示→Watchdog状態確認までをその場で試せる。実VLM/実HIDの代わりに決定的な `DemoModelClient`/`DemoGame` を既定で使用するため、APIキーやドライバなしで即座に動作する。

```bash
.venv/Scripts/pip install -e ".[gui]"
.venv/Scripts/python -m vlm_auto_replay.gui
```

`http://127.0.0.1:8765` を開く。デザインは `VLMAutoReplayTool_UI_DESIGN.md`(Sentri風トークン)の配色・タイポグラフィ・カード/ボタンコンポーネントに準拠している(ダークキャンバス+lime強調チップ+Rubikフォント)。

実運用のモデル/HIDに差し替える場合は、`vlm_auto_replay.gui.server` をimportする前に `configure_model_client()` で実クライアントを設定しておけばよい(GUI側のコード変更は不要)。

## モデル・HIDバックエンドの差し替え

- 基盤モデル呼び出しは `prompts/model_client.py` の `FoundationModelClient` Protocolを実装し、`configure_model_client()` で注入する。テストでは決定的な `ScriptedFoundationModelClient` を使用している。実運用ではVLM APIをラップした実装を注入する(ベンダー名はループ本体に埋め込まない)。
- HID実行は `actions/api_primitives.py` の `PadBackend` / `KeyboardMouseBackend` / `OcrBackend` を実装して差し替える。実機用に `ViGEmPadBackend`(ViGEm経由)・`SendInputBackend`(ctypes経由)を用意し、テスト・未接続環境向けに `NullPadBackend` 等を用意している。

## ディレクトリ構成

```
src/vlm_auto_replay/
  prompts/     Phase1 プロンプトテンプレート層
  loop/        Phase2 メインループ+StepLog、Phase5 Watchdog
  actions/     Phase3 Skill/API実行層+HIDプリミティブ+サンドボックス
  knowledge/   Phase4 RAGクライアント+Experience
  skills/      Phase6 スキル自動抽出+受け入れゲート
  navigation/  Phase7 視覚的自己位置推定パイプライン
  gui/         操作用Webダッシュボード(FastAPI+静的フロントエンド)
tests/
  fixtures/fake_game.py  Final Phase向け決定的フェイクゲーム
```
