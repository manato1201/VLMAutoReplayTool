# 第 3 回 Watchdog: 停滞を見つけて、計画を作り直す

> 所要時間の目安: 2.5 時間 / 前提: 第 0〜2 回

## この回のゴール

- 自動プレイが**スタックする**(同じことを繰り返す)問題と、Watchdog の役割を説明できる。
- 再構築を起こす **2 つの条件**(OR)と、その理由を説明できる。
- 再構築の入力を**絞る**(現在の TODO とその過去の試行ログだけ)理由を説明できる。

## 1. 停滞

ゲームの自動プレイは、**壁に当たり続ける**ことがあります(同じ場所で、同じ行動を繰り返す)。モデルは、状況を「少しずつ違う」と思って、**ずっと同じことを試し続ける**かもしれません。放っておくと、**時間もコストも**無駄になります。

**Watchdog**(番犬)は、ループを**見張り**、停滞と判断したら、**ループを止めて、TODO を作り直させます**。

```python
# 引用: src/vlm_auto_replay/loop/watchdog.py::Watchdog.should_intervene
def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
    same_todo_logs = [log for log in logs if log.todoId == todo.todoId]

    # 条件2: 同一TODOがNステップ以上継続(強制発動)
    if len(same_todo_logs) >= self._threshold:
        return True

    # 条件1: diagnose_stallが回復不能と判断
    diagnosis = diagnose_stall(todo, self._as_attempt_log(same_todo_logs))
    self._last_diagnosis = diagnosis
    return diagnosis.shouldRebuildTodo
```

## 2. 2 つの条件(どちらか一方で発動)

| 条件 | しくみ | 狙い |
|---|---|---|
| **① 診断** | `diagnose_stall`(モデル)が「**回復不能**」と判断(`shouldRebuildTodo`) | 賢い判断: 少ない試行でも、行き詰まりを察する |
| **② 強制** | **同じ TODO が N ステップ以上**続いた(既定 15) | 安全網: モデルの判断が**甘く**ても、必ず止まる |

**①と②を OR にする理由**: モデルの診断は**間違える**(行き詰まりを見逃す)。それでも**必ずどこかで止まる**ように、**数えるだけの単純な上限**(②)を、保険として置いています。**判断の賢さ**と**止まる保証**を、別々の仕組みで持つ。

**順序**: ②(数えるだけ)を**先に**調べ、満たしていれば、モデルを**呼ばずに**介入します(モデルの呼び出しは高い)。強制発動の経路では、`last_diagnosis` は更新されません(`None` のままのことがある)。

一時的な失敗では、**すぐに再構築しません**(診断が「回復可能」なら、続ける)。

```python
# 実行できる
from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.loop.watchdog import Watchdog
from vlm_auto_replay.prompts.model_client import ScriptedFoundationModelClient, configure_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import StallDiagnosis, TodoItem


def logs(n, todo_id="t1"):
    return [StepLog(stepIndex=i, timestamp="2026-11-01T00:00:00+00:00", todoId=todo_id, observationRef=f"obs:{i}",
                    reasoning="壁に向かう", actionTaken={"type": "api", "id": "key_input", "params": {}},
                    resultObservationSummary="変化なし") for i in range(n)]


todo = TodoItem(todoId="t1", description="橋を渡る", doneCriteria="向こう岸に着く")
client = ScriptedFoundationModelClient()
configure_model_client(client)

# ① 診断が「回復不能」: 3 ステップでも介入する
client.queue("diagnose_stall", StallDiagnosis(rootCause="橋が壊れている", nextApproach="別の道を探す", shouldRebuildTodo=True))
watchdog = Watchdog(stall_step_threshold=15)
print("診断が回復不能:", watchdog.should_intervene(todo, logs(3)), "/ 原因:", watchdog.last_diagnosis.rootCause)

# 診断が「回復可能」: 続ける
client.queue("diagnose_stall", StallDiagnosis(rootCause="操作がずれた", nextApproach="もう一度", shouldRebuildTodo=False))
print("診断が回復可能:", watchdog.should_intervene(todo, logs(3)))

# ② 強制: 15 ステップに達したら、モデルを呼ばずに介入
calls_before = len(client.calls)
print("15 ステップ(強制):", Watchdog(stall_step_threshold=15).should_intervene(todo, logs(15)),
      "/ モデルを呼んだ回数:", len(client.calls) - calls_before)

# 別の TODO のログは、数に入れない
mixed = logs(14, "t1") + logs(5, "t2")
client.queue("diagnose_stall", StallDiagnosis(rootCause="-", nextApproach="-", shouldRebuildTodo=False))
print("t1 が 14 件 + t2 が 5 件:", Watchdog(stall_step_threshold=15).should_intervene(todo, mixed))
reset_model_client()
```

```text
# 出力
診断が回復不能: True / 原因: 橋が壊れている
診断が回復可能: False
15 ステップ(強制): True / モデルを呼んだ回数: 0
t1 が 14 件 + t2 が 5 件: False
```

## 3. TODO の再構築: 入力を絞る

```python
# 引用: src/vlm_auto_replay/loop/watchdog.py::Watchdog.rebuild_todo
def rebuild_todo(
    self, current_todos: list[TodoItem], current_todo: TodoItem, logs: list[StepLog]
) -> list[TodoItem]:
    """再構築の入力は「現在のTODOリスト + そのTODOの過去試行ログ」のみ。

    セッション全体ログは渡さない(スコープの厳密化)。RAGクライアントは一切
    使用しない(rag_context には現在TODOの過去ログを文字列化したものだけを渡す)。
    """
    attempt_log = [log for log in logs if log.todoId == current_todo.todoId]
    return decompose_goal_to_todo(
        goal=self._reconstruct_goal(current_todos, current_todo),
        rag_context=[self._serialize_log(log) for log in attempt_log],
    ).todos
```

再構築の入力は、**現在の TODO のリスト + その TODO の過去の試行ログ**だけ。

- **セッション全体のログは渡さない**: 長くなり、**関係のない出来事**が判断を濁す。スコープを厳密にする。
- **RAG は使わない**: `decompose_goal_to_todo` の `rag_context` には、**現在の TODO の過去ログを文字列にしたもの**だけを渡す(知識を新たに検索しない。第 1 回の境界)。
- 「**何が起きて停滞したか**」を目標の文に埋め込む(`_reconstruct_goal`): 「次の TODO で停滞したため再計画する: …(他の未完了 TODO: …)」。

```python
# 実行できる
from vlm_auto_replay.loop.schemas import StepLog
from vlm_auto_replay.loop.watchdog import Watchdog
from vlm_auto_replay.prompts.model_client import ScriptedFoundationModelClient, configure_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import DecomposeGoalOutput, TodoItem

stuck = TodoItem(todoId="t1", description="橋を渡る", doneCriteria="向こう岸に着く")
other = TodoItem(todoId="t2", description="ボスを倒す", doneCriteria="勝利画面")
logs = [StepLog(stepIndex=i, timestamp="2026-11-01T00:00:00+00:00", todoId="t1" if i < 3 else "t2", observationRef="o",
                reasoning="r", actionTaken={"type": "api", "id": "key_input", "params": {}}, resultObservationSummary=f"結果{i}")
        for i in range(5)]

client = ScriptedFoundationModelClient()
client.queue("decompose_goal_to_todo", DecomposeGoalOutput(
    todos=[TodoItem(todoId="t1b", description="迂回路を探す", doneCriteria="別の道が見つかる")], ragContextUsed=[]))
configure_model_client(client)

new_todos = Watchdog().rebuild_todo([stuck, other], stuck, logs)
payload = client.calls[0]["payload"]
print("新しい TODO:", [t.description for t in new_todos])
print("モデルへの目標:", payload["goal"])
print("渡した過去ログの数:", len(payload["rag_context"]), "(t1 の 3 件だけ。t2 の 2 件は含めない)")
reset_model_client()
```

```text
# 出力
新しい TODO: ['迂回路を探す']
モデルへの目標: 次のTODOで停滞したため再計画する: 橋を渡る(他の未完了TODO: ボスを倒す)
渡した過去ログの数: 3 (t1 の 3 件だけ。t2 の 2 件は含めない)
```

## 4. 見る・調整する

- **閾値**(`stall_step_threshold`)は、ゲームの種類で決める。**短すぎる**と、まだ試せる状況で再構築してしまい、**長すぎる**と、無駄が増える。既定の 15 は、**初期値**(実運用のデータで調整したものではない)。
- `last_diagnosis` は、**画面の HUD が原因を表示する**ための、読み取り専用の値。`should_intervene` の判定には使わない。

## 確認問題

1. 再構築の条件を、①診断と②強制の OR にするのは、なぜですか。
2. ②を先に調べて、満たせばモデルを呼ばないのは、なぜですか。
3. 再構築の入力に、セッション全体のログを渡さないのは、なぜですか。
4. 一時的な失敗で、すぐに再構築しないのは、なぜですか。
5. 閾値 15 は、どう決めるべきですか。

## 解答

1. モデルの診断は**間違える**(見逃す)。**数えるだけの上限**を保険にして、判断が甘くても、**必ずどこかで止まる**ようにする。
2. モデルの呼び出しは**遅く・高い**。数えるだけで足りるなら、呼ばない。
3. 関係のない出来事が、判断を**濁す**。入力を、その TODO の試行に絞って、精度とコストを守る。
4. 壁に当たることは普通にある。**少しの失敗**で計画を作り直すと、**行ったり来たり**になる。診断が「回復不能」と言うか、上限に達してから。
5. **ゲームごとの実測**で。短すぎると、試せる状況で止まり、長すぎると無駄が増える。既定値は初期値。

## まとめ

- Watchdog は停滞を見張り、**診断(賢い)か、強制(数える)**のどちらかで、再構築を起こす。
- 再構築の入力は、**現在の TODO とその試行ログだけ**。RAG は使わない。
- 次の回は、実行の層(API の入力・スキル)と、**サンドボックス**。
