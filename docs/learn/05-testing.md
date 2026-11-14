# 5. テストの書き方と実行

## 実行

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev,gui]"
.venv/Scripts/pytest -q                  # 全部(文書の検査を含む)
.venv/Scripts/pytest tests/test_watchdog.py -q
.venv/Scripts/pytest -k "sandbox" -q
```

`pyproject.toml` の `testpaths = ["tests"]`。`tests/conftest.py` が `src/` を import パスに足します(`pip install -e` をしなくても動く)。

## テストの置き場所と、何を確かめるか

| ファイル | 確かめるもの |
|---|---|
| `test_prompts.py` | 9 つの関数の入出力・スキーマの検証 |
| `test_main_loop.py` | 1 ステップの流れ・変化の確認・理由が空なら止まる |
| `test_watchdog.py` | 2 つの条件・再構築の入力の絞り込み |
| `test_skill_runner.py` | procedure(ガイダンスで再実行)・script(サンドボックス)・許可リスト |
| `test_rag_boundary.py` | `functions.py` が `rag_client` を import しない |
| `test_skill_extraction.py`・`test_navigation.py` | 抽出・ゲート・自己位置推定 |
| `test_final_integration.py` | **偽のゲーム**での通し |
| `test_gui_server.py`・`test_gui_hud.py` | 操作 GUI(`TestClient`)・HUD(WebSocket) |
| `test_gui_auth.py`・`test_gui_audit.py`・`test_gui_security.py`・`test_gui_debug_gate.py`・`test_gui_admin_split.py` | 認証・役割・監査・Host/Origin・デバッグの門・管理画面の分離 |
| `test_docs.py` | 文書の検査(下) |

## よく使う型

**1. 偽のモデル**(`scripted_client` フィクスチャ / `ScriptedFoundationModelClient`): `task` ごとに、**期待する答えを順に登録**する。登録のない `task` が呼ばれたら**失敗**(想定外の呼び出しに気づく)。

**2. 偽のゲーム・偽の部品**(`fixtures/fake_game.py`・`fixtures/helpers.py`): 状態を持ち、`apply(action)` で進む。**決定的**なので、何度でも同じ結果。

**3. Null バックエンド**: `NullPadBackend` などは、呼ばれた内容を**覚えるだけ**。`assert backend.calls == [...]` で確かめる。

**4. GUI は `TestClient`**: `client` フィクスチャが、`RuntimeState(db_path=":memory:")` に差し替える(実ユーザーの DB を触らない)。`conftest.py` の自動フィクスチャが、`VLM_ALLOWED_HOSTS`(`testserver`)と `VLM_AUDIT_LOG`(一時ファイル)を設定する。

**5. 環境変数は `monkeypatch.setenv`**。

**6. 異常系と「しないこと」**: 理由が空・未知の API・procedure の 1 ステップ実行・ホールドアウトが空・RAG を行動の生成に使わない・viewer が実行を開始(403)。

## 新しい機能のテストを書く手順

1. **失敗するテストを先に**。2. 実装して通す。3. **異常系と「しないこと」**。4. **モデルの出力が壊れたとき**(`ValidationError`)の挙動。

## 文書の検査

```bash
python tools/check_docs.py             # 検査(標準ライブラリだけ)
python tools/check_docs.py --update    # 引用の更新・実行例の出力の作り直し
python tools/check_mermaid.py          # Mermaid が描画できるか(mmdc が要る)
python tools/make_diagrams.py          # 構成図(SVG)を作り直す
```

`tests/test_docs.py` は、同じ検査を pytest で行います(引用がソースと一致・実行例が 2 回動いて出力が一致・講義の標準の節・リンク・Mermaid の標準行)。実行例は、`tools/docs_pythonpath.txt` の `src` を import パスに足して動かします。

> **実行例を書くコツ**: 出力は決まった値だけに。UUID・時刻・一時パスをそのまま `print` しない。

## 確かめていないこと(正直に)

- **ViGEm・SendInput の実機での入力**は、自動テストでは確かめていない(Null 実装で、呼び出しの配線だけ)。
- **本物の VLM** での挙動・コスト・遅さは**未測定**(偽のモデルでの検証)。
- **Phase 6・7**(スキルの自動抽出・自己位置推定)は軽量実装で、実データでの精度は**未評価**。
- Watchdog の既定の閾値(15)は**初期値**(実運用のデータで調整していない)。
