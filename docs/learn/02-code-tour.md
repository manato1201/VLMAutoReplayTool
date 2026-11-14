# 2. コードの読み方(どこに何があるか)

## ソース(`src/vlm_auto_replay/`)

| ファイル | 行数の目安 | 役割 | 読む順 |
|---|---|---|---|
| `prompts/schemas.py` | 60 | 9 つの関数の入出力(Pydantic) | ① |
| `prompts/model_client.py` | 90 | `FoundationModelClient`(Protocol)・偽のモデル・差し替え | ② |
| `prompts/functions.py` | 140 | 9 つの型つきの関数(`rag_client` を import しない) | ③ |
| `loop/main_loop.py` | 125 | **MainLoop**(撮る→選ぶ→実行→確認) | ④ |
| `loop/schemas.py` | 25 | `StepLog` | |
| `loop/watchdog.py` | 70 | **Watchdog**(診断・強制・TODO の再構築) | ⑤ |
| `actions/skill.py` | 40 | `Skill`(procedure / script の検証) | |
| `actions/dispatcher.py` | 50 | **ActionDispatcher**(許可リスト) | ⑥ |
| `actions/api_primitives.py` | 210 | HID(ViGEm・SendInput)・Null 実装・OCR・`ApiPrimitives` | |
| `actions/sandbox.py` | 50 | **ScriptSandbox**(ブロックリスト方式) | ⑦ |
| `actions/skill_runner.py` | 40 | procedure(ガイダンスで再実行)と script(サンドボックス) | |
| `knowledge/experience.py`・`rag_client.py` | 60 | `decompose_goal_with_rag`・RAG のクライアント | |
| `skills/extraction.py`・`gate.py` | 70・40 | スキルの自動抽出・ホールドアウトの受け入れ | |
| `navigation/localizer.py`・`screen_state.py` | 120 | 4 段の自己位置推定(粗い検索→採点→位置決め→確認) | |
| `gui/` | 約 1900 | 操作 GUI(`server.py`・`runtime.py`)・SQLite(`persistence.py`)・HUD(`hud_broadcast.py`)・認証(`auth.py`・`auth_routes.py`)・監査(`audit.py`)・Host/Origin(`security.py`)・デモ(`demo.py`) | |

**読み方**: ①②③(型とモデルの入口)→ ④ループ → ⑤Watchdog → ⑥⑦実行の層。`gui/` は最後(デモの実行と、認証・監査)。

## テスト(`tests/`)

`fixtures/helpers.py`(共有の偽物: `InMemoryObservationStore`・`InMemoryStepLogSink`・`NeverInterveneWatchdog`・`FakeGameExecutor`・`make_null_api`)と、`fixtures/fake_game.py`(決定的な偽のゲーム)が、ほとんどのテストの土台です。`conftest.py` が `src/` を import パスに足し、`scripted_client`(偽のモデル)を用意します。

## 設計書・文書

| 場所 | 内容 |
|---|---|
| `VLMAutoReplayTool_DESIGN.md` | 設計書(Phase 0〜7) |
| `VLMAutoReplayTool_UI_DESIGN.md` | UI のデザイントークン(Sentri 風) |
| `IMPROVEMENT_DESIGN_2026-10.md`・`IMPROVEMENT_PLAN_2026-10.md` | 2026-10 の改善の設計と計画 |
| `docs/TECHNICAL_OVERVIEW.md` | 各 Phase のアーキテクチャ図・シーケンス図 |
