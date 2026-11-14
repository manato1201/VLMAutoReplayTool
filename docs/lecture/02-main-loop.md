# 第 2 回 メインループ: 撮る → 選ぶ → 動かす → 確かめる

> 所要時間の目安: 3 時間 / 前提: 第 0〜1 回

## この回のゴール

- メインループの **1 ステップ**で何が起き、何が**必ず**記録されるかを説明できる。
- **固定の待ち時間(sleep)で進めず、画面の変化を確かめてから進む**理由を説明できる。
- 部品(撮影・保存・実行・見張り)を**注入**する作りと、その利点(偽物でテストできる)を説明できる。
- **理由が空なら実行しない**規則(監査可能性)を、動かして確かめられる。

## 1. ループの本体

```python
# 引用: src/vlm_auto_replay/loop/main_loop.py::MainLoop.run
def run(self, todo: TodoItem, max_steps: int, guidance_text: str | None = None) -> list[StepLog]:
    logs: list[StepLog] = []
    for step_index in range(max_steps):
        observation = self._capture.capture()
        next_action = generate_next_action(
            todo, observation, self._recent_history(logs), guidance_text=guidance_text
        )
        reasoning = explain_action_choice(next_action, {"todo": todo.model_dump(), "step": step_index})
        if not reasoning.reasoning.strip():
            raise AssertionError(
                "reasoning が空文字列です。行動前に必ずreasoningをログする要件(監査可能性)に違反します。"
            )

        log = StepLog(
            stepIndex=step_index,
            timestamp=self._now(),
            todoId=todo.todoId,
            observationRef=self._observation_store.store(observation),
            reasoning=reasoning.reasoning,
            actionTaken={
                "type": next_action.actionType,
                "id": next_action.actionId,
                "params": next_action.params,
            },
            resultObservationSummary="",
        )

        before = observation
        self._executor.execute(next_action)
        after = self._capture.capture()
        log.resultObservationSummary = summarize_screen_change(before, after).summary

        logs.append(log)
        self._step_log_sink.log_step(log)

        if self._watchdog.should_intervene(todo, logs):
            break
        if self._todo_done_checker(todo, after):
            break
    return logs
```

順に読みます。

1. **撮る**(`capture`)。
2. **次の行動を生成**(`generate_next_action`): TODO・画面・**直近 N 件の履歴**(`history_window`、既定 5)・非強制のガイダンス。
3. **理由を生成**(`explain_action_choice`)。**空文字なら `AssertionError`**: 行動の**前**に、必ず理由を残す(監査できないものは、実行させない)。
4. `StepLog` を作る(ステップ番号・時刻・TODO・撮った画像の参照・理由・行動)。
5. **実行**(`executor.execute`)→ **もう一度撮る**。
6. **前後 2 枚の画像から、変化を要約**(`summarize_screen_change`)→ `StepLog` に書く。
7. ログを保存(`step_log_sink`)。
8. **Watchdog が介入を求める**、または **TODO の完了判定が真**なら、ループを抜ける。上限は `max_steps`。

> **固定のディレイで進めない**: 「押したら 1 秒待って次へ」ではなく、**撮り直した画面の変化を要約**して、状態が進んだかを見る(`summarize_screen_change`)。ゲームの読み込みの長さに依らず、**起きたことを見てから**進む。

## 2. 部品を「注入」する

`MainLoop` は、自分では何もしません。**5 つの部品**を、コンストラクタで**受け取ります**(`Protocol`)。

| 部品 | 約束(`Protocol`) | 本番 | テスト・デモ |
|---|---|---|---|
| `capture` | `capture() -> bytes` | 画面キャプチャ | 画像のバイト列を返す偽物 |
| `observation_store` | `store(bytes) -> str` | ファイル保存 | メモリに溜める |
| `step_log_sink` | `log_step(StepLog)` | SQLite など | メモリに溜める |
| `executor` | `execute(NextActionOutput)` | `ActionDispatcher` | 偽のゲーム |
| `watchdog` | `should_intervene(todo, logs)` | `Watchdog` | 「介入しない」偽物 |

**ループのコードは、本物でも偽物でも同じ**。だから、**ゲームも、モデルも、画面も無い**環境で、**ループの全体**を決定的に確かめられます。

```python
# 実行できる
from vlm_auto_replay.loop.main_loop import MainLoop
from vlm_auto_replay.prompts.model_client import ScriptedFoundationModelClient, configure_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import (
    ExplainActionOutput, NextActionOutput, ScreenChangeSummary, TodoItem,
)


class FakeScreen:
    """撮るたびに、番号の違うバイト列を返す偽の画面。"""
    def __init__(self):
        self.count = 0
    def capture(self) -> bytes:
        self.count += 1
        return f"frame-{self.count}".encode()


class Memory:
    def __init__(self):
        self.items = []
    def store(self, observation: bytes) -> str:
        self.items.append(observation)
        return f"obs:{len(self.items) - 1}"
    def log_step(self, log):
        self.items.append(log)


class Recorder:
    def __init__(self):
        self.actions = []
    def execute(self, action):
        self.actions.append(action.actionId)


class Never:
    def should_intervene(self, todo, logs):
        return False


client = ScriptedFoundationModelClient()
for key in ("W", "D", "SPACE"):
    client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="key_input", params={"key": key, "hold_ms": 50}))
    client.queue("explain_action_choice", ExplainActionOutput(reasoning=f"{key} で進むため"))
    client.queue("summarize_screen_change", ScreenChangeSummary(summary=f"{key} を押して、画面が少し動いた"))
configure_model_client(client)

store, sink, executor = Memory(), Memory(), Recorder()
done_after = {"n": 0}


def done(todo, observation) -> bool:      # 3 ステップ目で完了とみなす
    done_after["n"] += 1
    return done_after["n"] >= 3


screen = FakeScreen()
loop = MainLoop(capture=screen, observation_store=store, step_log_sink=sink, executor=executor,
                watchdog=Never(), todo_done_checker=done)
todo = TodoItem(todoId="t1", description="右へ進む", doneCriteria="ゴールが見える")
logs = loop.run(todo, max_steps=10)

print("実行したステップ数:", len(logs), "(上限 10 の前に、完了で止まった)")
for log in logs:
    print(f"  step {log.stepIndex}: 理由={log.reasoning} / 行動={log.actionTaken['params']['key']} / 結果={log.resultObservationSummary}")
print("撮影した回数:", screen.count, "/ 保存した画像:", len(store.items), "← 1 ステップに 2 回撮り、保存するのは「行動の前」の 1 枚")
reset_model_client()
```

```text
# 出力
実行したステップ数: 3 (上限 10 の前に、完了で止まった)
  step 0: 理由=W で進むため / 行動=W / 結果=W を押して、画面が少し動いた
  step 1: 理由=D で進むため / 行動=D / 結果=D を押して、画面が少し動いた
  step 2: 理由=SPACE で進むため / 行動=SPACE / 結果=SPACE を押して、画面が少し動いた
撮影した回数: 6 / 保存した画像: 3 ← 1 ステップに 2 回撮り、保存するのは「行動の前」の 1 枚
```

注目: 1 ステップで画面を **2 回**撮ります(行動の前と後)。保存するのは**行動の前**の画像(`observationRef`)で、後の画像は、**変化の要約**に使います。

## 3. 理由が空なら、実行しない

```python
# 実行できる
from vlm_auto_replay.loop.main_loop import MainLoop
from vlm_auto_replay.prompts.model_client import ScriptedFoundationModelClient, configure_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import ExplainActionOutput, NextActionOutput, TodoItem


class Screen:
    def capture(self) -> bytes:
        return b"frame"


class Memory:
    def store(self, observation): return "obs:0"
    def log_step(self, log): pass


class Recorder:
    def __init__(self):
        self.executed = 0
    def execute(self, action):
        self.executed += 1


class Never:
    def should_intervene(self, todo, logs): return False


client = ScriptedFoundationModelClient()
client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="key_input", params={}))
client.queue("explain_action_choice", ExplainActionOutput(reasoning="   "))      # 空白だけの理由
configure_model_client(client)

executor = Recorder()
loop = MainLoop(capture=Screen(), observation_store=Memory(), step_log_sink=Memory(), executor=executor,
                watchdog=Never(), todo_done_checker=lambda todo, obs: False)
try:
    loop.run(TodoItem(todoId="t", description="x", doneCriteria="y"), max_steps=3)
except AssertionError as exc:
    print("止まった:", str(exc)[:20], "…")
print("実行された行動の数:", executor.executed, "← 理由が無いので、何も実行されない")
reset_model_client()
```

```text
# 出力
止まった: reasoning が空文字列です。行動 …
実行された行動の数: 0 ← 理由が無いので、何も実行されない
```

> **順序が大事**: 理由の検査は、**実行の前**(`execute` の前)にあります。「実行してから、理由が無いと気づく」のでは、手遅れです。

## 4. 履歴の窓

モデルに渡す履歴は、**直近 N 件**(`history_window`)だけです。全部を渡すと、**長くなりすぎて**(モデルの入力の上限・コスト)、古い情報が**ノイズ**になります。`history_window=0` なら、履歴なし。

## 確認問題

1. 1 ステップで、画面を 2 回撮るのは、なぜですか。
2. 「押したら 1 秒待つ」でなく、変化の要約で進むのは、なぜですか。
3. 理由が空のとき、行動の実行の**前**に止めるのは、なぜですか。
4. `MainLoop` が、部品をコンストラクタで受け取る利点は。
5. モデルに渡す履歴を、直近 N 件に絞るのは、なぜですか。

## 解答

1. **行動の前後の画面**を比べて、**何が変わったか**を要約するため(行動が効いたかの確認)。
2. 待ち時間は、**ゲームの読み込みの長さ**に依存する(短すぎれば進まず、長すぎれば無駄)。変化を**見て**進めば、状況に合わせられる。
3. 実行してからでは、**説明のつかない操作**が、すでにゲームに起きてしまう。監査できない行動は、起こさない。
4. 本物(画面・ゲーム・モデル)なしで、**偽物を渡してテスト**できる。ループのコードを変えずに、部品を差し替えられる。
5. 長くなりすぎて、**入力の上限とコスト**を超える。古い情報は、判断のノイズになる。

## まとめ

- 1 ステップ = 撮る → 行動と**理由**を生成 → ログ → 実行 → 撮り直す → **変化を要約**。理由が空なら、実行しない。
- 固定の待ちでなく、**変化を見て**進める。
- 部品は**注入**(`Protocol`)。偽物で、ループ全体を決定的にテストできる。
- 次の回は、**停滞**を見つけて TODO を作り直す Watchdog。
