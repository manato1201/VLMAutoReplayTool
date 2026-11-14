# 6. トラブルと保守

## 症状 → 原因 → 直し方

| 症状 | 原因 | 直し方 |
|---|---|---|
| ブラウザで **403**「このHostでは…」 | `127.0.0.1`・`localhost` 以外の名前で開いている | `VLM_ALLOWED_HOSTS` に、その名前を足す |
| 画面からの操作が **403**(Origin) | Host とポートが違う URL から送っている | 同じ URL(Host・ポート)で開く |
| **401** | キー無し・間違い | `X-API-Key` を付ける。ブラウザならログイン |
| **403**(役割) | 役割が足りない(実行の開始は operator、スキル・HID は admin) | 役割を上げる。スキル・HID は `/admin.html` から |
| 起動しない(`VLM_AUTH=required`) | ユーザーが 1 人もいない | `python -m vlm_auto_replay.gui.auth add --name <名前> --role admin` |
| 「既に実行中です」 | 同時に 1 つしか走らせない | 停止してから、もう一度開始 |
| 実行の STATUS が `INTERVENED` | Watchdog が介入した(デモの「Watchdog 介入」、または本物の停滞) | HUD・Watchdog パネルの理由を見る。TODO は自動で再構築される |
| HUD(`/hud.html`)が **404** | 本番(`required`)では既定で無効 | 必要なときだけ `VLM_DEBUG=1`(admin だけが使える) |
| 履歴の画像が出ない | 観測の画像は**メモリ**。再起動・次の Run で消える | 仕様。残したいなら、画像の保存先(ファイル)を足す(レシピ参照) |
| 「vgamepad が未インストールです」 | ViGEm を選んだが、`vgamepad` が無い | `pip install -e ".[hid]"` + ViGEmBus のドライバ。選択は直前のまま変わらない |
| **GUI で HID を選んでも、実機が動かない** | **GUI の実行はデモのゲームだけ**(`DemoExecutor`)。HID の設定は、実行に使われない | 仕様(未配線)。実機で動かすなら、ライブラリとして `MainLoop` を使う |
| スキルの追加で、危険なコードを入れられないか心配 | script はサンドボックスで動くが、**ブロックリスト方式で完全ではない** | スキルの追加は admin だけ。他人のコードは入れない(講義第 4 回) |

## 既知の問題(コードは変えていない。直すときの手がかり)

文書を作るときに、コードを読んで・動かして見つけたものです。

### 1. サンドボックスは、`import builtins` と `__globals__` で破れる

`_guarded_import` は、**禁止するモジュールの列挙**(ブロックリスト)で、`builtins` が入っていません。`import builtins` のあと `builtins.__import__('os')` で、**禁止された `os` を取り出せます**。また、`api.__class__.__init__.__globals__` で、`api` を定義したモジュールの名前空間(`ctypes` など)に届きます(講義[第 4 回](../lecture/04-actions-and-sandbox.md)で、動かして確かめた)。

ソースのコメントも、「完全な安全境界ではない」と明記しています。**実用上のリスクは**、script スキルを**登録できる人**(admin)と、**自動生成のコード**(受け入れゲートと人の確認)に依存します。**直す案**: 許可リスト方式・`api` を薄い代理に・AST の検査・OS の分離([4 拡張](04-extend.md) レシピ 4)。

### 2. 操作 GUI の実行は、デモのゲーム・デモのモデルだけ

`start_run` は `DemoGame` / `DemoExecutor` を使い、HID の設定は実行に使われません(コードのコメントにも、そう書かれている)。GUI から実機・実モデルを動かすことは、まだできません。README の「操作用 GUI」は、**デモで動作を確認する**ためのものです。

### 3. 観測の画像は、メモリだけ(次の実行・再起動で消える)

履歴(StepLog)は SQLite に残りますが、`observationRef` が指す画像は残りません。過去の Run の「画面」は、再起動後は見られません。**直す案**: 画像をファイルに保存し、`observationRef` をそのパスにする(保存先と、容量の管理が要る)。

### 4. Phase 6・7 は軽量実装

`_infer_param_schema` は**空の辞書**を返す。procedure の手順書は、**各ステップの理由を連結しただけ**。自己位置推定の特徴量は、**軽量のハッシュ**。いずれも `TODO(…反復)` のコメントが付いている。

### 5. 古い README の数字

旧 README(`docs/reference/setup-and-operations.md`)には、「自動テスト(40件)」「`68 passed`」と書かれていますが、今のテストの件数と合っていません(書かれた時点の数字)。今の件数は `pytest` で確かめてください。

### 6. その他の制約(設計上の割り切り)

- **Watchdog の閾値 15** は初期値(実運用のデータで調整していない)。
- **ViGEm・SendInput** は実機のドライバが要り、このリポジトリのテストでは**未検証**(Null 実装で配線だけ)。
- **本物の VLM** での挙動・コスト・遅さは、**未測定**。
- GUI は **1 つのグローバルな `state`**(同時に 1 つの Run)。

## 定期の保守

| いつ | 何を |
|---|---|
| 毎回の変更 | `.venv/Scripts/pytest -q` と `python tools/check_docs.py` |
| 月 1 回 | 依存の更新(`pydantic`・`fastapi`・`pyjwt`・`websockets`)→ テスト |
| 月 1 回 | `~/.vlm_auto_replay/gui.sqlite3`・`audit.jsonl` の**大きさ**(履歴は消えない。ローテーションは無い) |
| 四半期 | ユーザー(`python -m vlm_auto_replay.gui.auth list`)の見直し。**登録済みスキル(特に script)**の見直し |
| 関数を直したとき | `python tools/check_docs.py --update` で、文書の引用・出力を作り直し、**説明が合っているか**を読む |
