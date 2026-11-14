# 4. 拡張のレシピ

どれも「**型 → 実装 → 許可リスト → テスト**」の順で、小さく進めます。**モデルの出力は信用しない**(入口で検証)のが原則です。

## レシピ 1: 新しい API の入力を足す

例: スティックの傾け(講義[第 5 回](../lecture/05-skills-testing-capstone.md)の総合演習)。

1. **`api_primitives.py`**: 必要なら `PadBackend` などの `Protocol` にメソッドを足し、`NullPadBackend`(呼ばれた内容を覚える)・実機の実装(`ViGEmPadBackend`)にも足す。
2. **`ApiPrimitives`** に、メソッドを足す。**引数の範囲を検査**する(モデルが返す値は信用しない)。
3. **`dispatcher.py` の `_ALLOWED_API_PRIMITIVES`** に、名前を**足す**(足し忘れると、モデルが選んでも `KeyError`)。
4. **モデルに伝える**: 使える入力の一覧(プロンプト側・`NextActionOutput` の説明)に、新しい入力を足す。知らない入力は選べない。
5. **テスト**: 範囲外(`ValueError`)・許可リスト・Null バックエンドへの記録・`ActionDispatcher` 経由の呼び出し。

## レシピ 2: 本物の VLM に差し替える

1. `FoundationModelClient`(`Protocol`)の `complete(*, task, payload, images, response_model)` を満たすクラスを作る。`task` に応じてプロンプトを組み立て、**画像を渡し**、**`response_model` で検証した値**を返す(`response_model.model_validate(...)`)。
2. 起動時に `configure_model_client(client)`(GUI は、未設定のときだけ `DemoModelClient`)。
3. **失敗の扱い**: モデルの出力が `ValidationError` になったら、**再試行**するか、ステップを失敗として記録するかを決める(今のループは、例外を `error` にして止める)。
4. **コスト・遅さ**: `generate_next_action` は毎ステップ呼ばれる。`history_window` を小さくする・画像を縮小する・`summarize_screen_change` の頻度を下げる、を、実測で決める。
5. **秘密**(API キー)は環境変数で渡し、リポジトリに書かない。ベンダー名をループの本体に埋め込まない(設計の約束)。
6. **テスト**: `ScriptedFoundationModelClient` と同じ形で、**HTTP を偽にした**統合テスト(本物の API を、テストで呼ばない)。

## レシピ 3: Watchdog の規則を足す

例: 「直近 N ステップの画面の要約が、すべて同じ」を 3 つ目の条件に。

1. `Watchdog.should_intervene` に、**数えるだけの条件**を足す(モデルを呼ばない条件は、**先に**調べる)。
2. **誤検知を抑える**: 「変化なし」が、待ちの場面(読み込み中)でも起きる。閾値と、除外(待つ行動のとき)を決める。
3. `last_diagnosis` は、診断の経路でだけ更新される(強制の経路では `None`)。HUD の表示で `None` を扱う。
4. **テスト**(`test_watchdog.py` の形): 条件ごとに、介入する・しない・モデルを呼ばない。

## レシピ 4: サンドボックスを強くする

現状は**ブロックリスト方式**で、`builtins` の import や `api.__class__...__globals__` で破れます(講義[第 4 回](../lecture/04-actions-and-sandbox.md))。

1. **`_guarded_import` を許可リスト**に: 許可するモジュール(例: `math`)だけ通し、他は `SandboxViolationError`(`builtins` は不可)。
2. **`api` を薄い代理**にする: 必要なメソッドだけを持つ小さなオブジェクト(`__class__` から元のモジュールへ辿れない形)を渡す。
3. **AST の検査**: 実行の前に、`ast` で、`__`(ダンダー)の属性アクセス・`import` を検査して弾く。
4. **登録を絞る**: script スキルの追加を admin だけにする(今も)。自動生成の script は、**ホールドアウトの受け入れ**と**人の確認**を通す。
5. **OS の分離**: 他人のコード・ネットで拾ったコードを動かすなら、別プロセス・コンテナ・ネットワークなしで。
6. **テスト**: 既知の抜け道(`import builtins`・`__globals__`)が、**止まる**ことを、テストにする。

## レシピ 5: スキルの抽出を強くする(Phase 6)

- `_infer_param_schema` を、AST で `params["x"]` を拾う実装に(今は空の辞書)。
- `_render_procedure_text` を、`paramSchema` と突き合わせたテンプレートに(今は理由の連結)。
- **受け入れゲート**: `replay_test` の実体(実ゲーム・偽のゲームでの再生)を注入する。**ホールドアウトのシナリオは、生成に使っていないもの**にする。

## レシピ 6: RAG を足す・替える(Phase 4)

- RAG は **`decompose_goal_with_rag` だけ**で使う(`functions.py` に持ち込まない。`test_rag_boundary.py` が守る)。
- 名前空間: `docs`(本文)・`experience`(`summary` だけを添付)。新しい名前空間を足すなら、**何を添付するか**(全文か要約か)を決める。

## レシピ 7: GUI の API を足す(役割を必ず宣言する)

`@app.post("...", dependencies=[auth.operator])` のように宣言する(宣言しないと、`VLM_AUTH=required` でも**誰でも通る**)。**入力を起動する操作は operator、コード・設定を変える操作は admin**。**テスト**: 役割ごとの 200 / 401 / 403・監査ログに残ること。

## 変更の前に

- **理由が空なら実行しない**・**RAG を行動の生成に持ち込まない**・**API は許可リストだけ**、の 3 つの約束を、崩さない。
- 変更したら `.venv/Scripts/pytest -q` と `python tools/check_docs.py`([5 テスト](05-testing.md))を通す。
