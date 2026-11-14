# 3. 内側のしくみ

講義(第 1〜5 回)で扱った核のほかに、**操作 GUI の実行の流れ・保存・HUD・認証と管理画面の分離**を説明します。

## 1. GUI の実行の流れ(`gui/runtime.py`)

`RuntimeState` が、GUI の状態(TODO・ログ・状態・スキル)を持ち、1 つのグローバルな `state`(`server.py`)として使われます。

1. **`decompose(goal)`**: ゴールを TODO に分解(デモのモデルが答える)。
2. **`start_run(todo_id, max_steps, stall_demo)`**:
   - **ロック**(`self.lock`)の中で、すでに実行中なら `RuntimeError`(同時に 1 つだけ)、未知の TODO なら `KeyError`。
   - `logs`・`observations` を**空にして**、状態を `running` に。
   - Run の ID を作り、DB に開始を記録、HUD に `run_started` を流す(**前の Run のログが混ざらない**ように、HUD が蓄積を消す合図)。
   - **別スレッド**(`daemon=True`)で `MainLoop.run` を走らせる。画面のリクエストは、すぐ返る。
3. スレッドの終わりで、状態を決める: エラー → `error`、Watchdog が介入 → `intervened`、停止要求 → `stopped`、それ以外 → `done`。`intervened` なら TODO を**再構築**、`done` なら完了を記録して**経験**を発行。
4. 結果を DB に記録(`finish_run`)。

**部品は、ライブラリのときと同じ**(`MainLoop` に注入): `_StateObservationStore`(画像をメモリへ)・`_LiveStepLogSink`(メモリ・DB・HUD へ)・`_StoppableWatchdog`(本物の `Watchdog` を包み、**停止ボタン**で介入を起こせる)。

> **観測の画像はメモリ**(`state.observations`)で、**次の `start_run` で空になる**。DB に残るのは StepLog(`observationRef` は残るが、再起動や次の Run の後は、画像を引けない)。

## 2. 停止ボタン(`_StoppableWatchdog`)

停止を、**ループの外からスレッドを殺さず**に実現するため、`Watchdog` を包んだ版が、`should_intervene` で「停止が要求されたか」を見ます。ループは、**1 ステップの終わりに**、いつもの介入の判定として停止を知る(途中で強制終了しない = 行動の途中で切れない)。

## 3. 保存(`gui/persistence.py`)

`SqlitePersistence`: スキル・Run・StepLog を SQLite に。**テストでは `:memory:`**(実ユーザーの DB を触らない)。`resolve_db_path` が既定(`~/.vlm_auto_replay/gui.sqlite3`)を決める。

## 4. 監視 HUD(`gui/hud_broadcast.py`・`static/hud.*`)

`HudBroadcaster.publish(event)` が、イベントを**キューに入れ**、アプリのイベントループ上のポンプが、**接続中の WebSocket 全員へ**配る。接続は `/ws/hud`。

- 配信は**ベストエフォート**(切れた接続は外す)。ループの実行スレッドが、WebSocket で**詰まらない**ように、キュー経由。
- **本番では既定で無効**(開発者向け): `VLM_AUTH=required` では、`/hud.html`・`/hud.js`・`/ws/hud` は、`VLM_DEBUG=1` で有効にしても **admin だけ**。無効のとき、ログイン済みの人には 404、WebSocket は 1008 で閉じる。

## 5. 認証・役割・監査

構造は ToolOrchestrationHub・VisualRegressionQATool(別リポジトリ)と同じ型です。

| 役割 | できること |
|---|---|
| `viewer` | 見る(状態・履歴・画像・HUD の購読) |
| `operator` | viewer + ゴールの分解・**実行の開始・停止**(端末へ実際にキー入力・クリックを送りうる) |
| `admin` | operator + **スキルの追加・削除**・**入力バックエンド(HID)の変更** |

- 既定は**認証なし**(`VLM_AUTH=off`)。Host の許可リスト(DNS rebinding)・変更操作の Origin(CSRF)・`/ws/hud` のハンドシェイクの検査は**常に**効く。
- **ユーザーが 0 人なら、`required` では起動しない**(fail-closed)。API キーは**ハッシュだけ**保存。ブラウザは HttpOnly・SameSite=Strict のクッキー(`/ws/hud` のハンドシェイクにも付く)。
- **監査ログ**: 変更系・拒否(401・403・`/ws/hud` の拒否)・ログイン・ログアウトを JSON Lines で。**本文・キー・クッキー・クエリは記録しない**。

### 管理画面の分離

スキルの追加・削除と HID の変更(admin)は、操作パネルではなく、**別ページ `/admin.html`(+ `/admin.js`)**に移してあります。`/admin.js` は `/static` の外に置き、**admin にしか配らない**(未ログインは `/login` へ・viewer / operator は 403。拒否は監査ログに載る)。`test_gui_admin_split.py` が確認します。

> **スキルの追加が admin だけなのは、重要**です。**script スキルは Python のコード**で、サンドボックスで実行される(第 4 回)。サンドボックスは**ブロックリスト方式で、完全な境界ではない**ので、**コードを登録できる人を、admin に絞る**ことが、防御の一部になっています。

## 6. ナビゲーション(Phase 7・`navigation/`)

画面から「いまどこにいるか」を推定する**4 段**: ①粗い検索(`DescriptorExtractor` の特徴量で、候補を絞る)→ ②採点 → ③位置決め → ④**最終確認**(`ApiPrimitives.ocr()` で、OCR のランドマークを照合し、**もっともらしいが誤った**候補を棄却する)。特徴量は、軽量なハッシュ(`SimpleHashDescriptorExtractor`)で、`DescriptorExtractor` を差し替えると、CNN の埋め込みなどに替えられる(`TODO(Phase7反復)`)。
