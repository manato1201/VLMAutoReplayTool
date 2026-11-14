# 第 5 回 スキルの抽出・受け入れ・テストと総合演習

> 所要時間の目安: 半日 / 前提: 第 0〜4 回

## この回のゴール

- スキルの**自動抽出**(重複操作の統合・動画と手順書からのコード生成)の流れを説明できる。
- **受け入れゲート**(ホールドアウトのテストを通ったものだけ登録)の考え方を説明できる。
- **RAG を使った TODO 生成**の流れ(`docs` と `experience` の 2 つの名前空間)を説明できる。
- 偽のモデルと偽のゲームで、**エンドツーエンドのテスト**を書き、機能を足せる。

## 1. スキルの自動抽出(Phase 6)

人が毎回スキルを書くのは大変です。**プレイのログから、モデルに作らせます**(`skills/extraction.py`)。

| 関数 | 何をするか |
|---|---|
| `extract_procedure_skill` | 似た操作の列を、**差分をパラメータにして 1 つに統合**(`merge_duplicate_operations`)→ procedure スキル |
| `extract_script_skill` | **動画と手順書**から Python のコードを生成(`generate_code_from_video_and_procedure`)→ script スキル |
| `extract_reusable_subroutines_from_sessions` | セッションをまたいで、**再利用できるサブルーチン**を切り出す。**メインループとは別のバッチ**で呼ぶ(ループを遅くしない) |

自動生成のスキルは、`createdBy="auto"` で、**`sourceTrace`**(どの動画・ログから作ったか)が必須です(第 1 回の検証)。後で、**間違ったスキルの出どころ**を追えます。

> **正直な現状**: Phase 6 は**軽量の実装**で、`TODO(Phase6反復)` のコメントが残っています。たとえば、`paramSchema` の推論は、**空の辞書を返すだけ**(`params["x"]` を AST で拾う本実装は未完)。手順書の文面も、**各ステップの理由を連結しただけ**。複数回の反復が前提の領域です。

## 2. 受け入れゲート: ホールドアウトで試してから

自動生成のスキルは、**そのまま登録しません**。**取っておいた別のシナリオ**(ホールドアウト)で試して、**すべて成功したら**登録します。

```python
# 引用: src/vlm_auto_replay/skills/gate.py::accept_skill
def accept_skill(
    candidate: Skill, holdout_scenarios: list[TodoItem], replay_test: ReplayTestRunner
) -> bool:
    """holdout_scenariosすべてに対するreplay_testが成功した場合のみTrueを返す。

    replay_testの実体(実際のreplay/liveテスト実行)は呼び出し側が注入する
    (実ゲーム/実VLMに依存するため、このモジュールでは実装しない)。
    """
    if not holdout_scenarios:
        raise ValueError("holdout_scenariosが空です。受け入れゲートを素通りさせないこと。")
    results = [ReplayResult(scenario, replay_test(candidate, scenario)) for scenario in holdout_scenarios]
    return all(r.succeeded for r in results)
```

- **ホールドアウト**: スキルを作るときに**使わなかった**シナリオ。作った状況でだけ動くスキルを、弾く(機械学習の**テストデータ**と同じ考え方)。
- **空なら、エラー**: シナリオが 0 件だと `all([])` が**真**になり、**何も試さずに合格**してしまう(**素通り**)。それを防ぐ検査。
- 実際に試す関数(`replay_test`)は**注入**する(実ゲーム・実モデルに依存するため、ここでは実装しない)。

```python
# 実行できる
from vlm_auto_replay.actions.skill import Skill
from vlm_auto_replay.prompts.schemas import TodoItem
from vlm_auto_replay.skills.gate import accept_skill

candidate = Skill(skillId="s-auto", gameTitle="Demo", type="script", scriptCode="api.key_input('W', 50)",
                  paramSchema={}, createdBy="auto", sourceTrace=["video:clip-1"])
scenarios = [TodoItem(todoId=f"h{i}", description=f"シナリオ{i}", doneCriteria="ゴール") for i in range(3)]

print("すべて成功     :", accept_skill(candidate, scenarios, lambda skill, scenario: True))
print("1 つだけ失敗   :", accept_skill(candidate, scenarios, lambda skill, scenario: scenario.todoId != "h1"))
print("すべて失敗     :", accept_skill(candidate, scenarios, lambda skill, scenario: False))

tried = []
accept_skill(candidate, scenarios, lambda skill, scenario: tried.append(scenario.todoId) or scenario.todoId != "h0")
print("失敗しても全部試す:", tried)

try:
    accept_skill(candidate, [], lambda skill, scenario: True)
except ValueError as exc:
    print("ホールドアウトが空:", str(exc)[:22], "…")
```

```text
# 出力
すべて成功     : True
1 つだけ失敗   : False
すべて失敗     : False
失敗しても全部試す: ['h0', 'h1', 'h2']
ホールドアウトが空: holdout_scenariosが空です。 …
```

## 3. RAG を使った TODO 生成(Phase 4)

`decompose_goal_with_rag` が、**RAG を使う唯一の入口**です(第 1 回の境界)。

1. `docs` の名前空間(マニュアル・wiki・攻略本)を検索。
2. `experience` の名前空間(`extract_experience` で抽出した**知見**)を検索。
3. **`docs` は本文(`text`)を、`experience` は `summary` だけ**を、文脈にする(`problem`・`betterWay` の全文は**添付しない**。長さと焦点を守る)。
4. `decompose_goal_to_todo(goal, rag_context)` を呼ぶ。

```python
# 実行できる
from vlm_auto_replay.knowledge.experience import decompose_goal_with_rag
from vlm_auto_replay.prompts.model_client import ScriptedFoundationModelClient, configure_model_client, reset_model_client
from vlm_auto_replay.prompts.schemas import DecomposeGoalOutput, TodoItem


class FakeRag:
    """namespace ごとに、決まった検索結果を返す偽の RAG。"""

    def __init__(self):
        self.queries = []

    def search(self, query, namespace):
        self.queries.append(namespace)
        if namespace == "docs":
            return {"results": [{"text": "ボスの弱点は背中", "id": "doc-1"}, {"text": ""}]}
        return {"results": [{"metadata": {"summary": "回避は 2 回まで", "problem": "長い説明", "betterWay": "長い対策"}}]}


client = ScriptedFoundationModelClient()
client.queue("decompose_goal_to_todo", DecomposeGoalOutput(
    todos=[TodoItem(todoId="t1", description="背中を狙う", doneCriteria="ダメージ")], ragContextUsed=["doc-1"]))
configure_model_client(client)

rag = FakeRag()
result = decompose_goal_with_rag("ボスを倒す", rag)
payload = client.calls[0]["payload"]
print("検索した名前空間:", rag.queries)
print("モデルに渡した文脈:", payload["rag_context"])
print("TODO:", [t.description for t in result.todos])
reset_model_client()
```

```text
# 出力
検索した名前空間: ['docs', 'experience']
モデルに渡した文脈: ['ボスの弱点は背中', '回避は 2 回まで']
TODO: ['背中を狙う']
```

空の `text` は捨てられ、`experience` からは **`summary` だけ**が渡っています。

## 4. テスト: 偽のモデルと偽のゲーム

`tests/` は、**本物のモデル・ゲーム・ドライバなし**で、全体を確かめます。

| 場所 | 確かめるもの |
|---|---|
| `test_prompts.py` | 9 関数の入出力・スキーマの検証 |
| `test_main_loop.py` | ループの 1 ステップ・変化の確認・理由が空なら止まる |
| `test_watchdog.py` | 2 つの条件・再構築の入力の絞り込み |
| `test_skill_runner.py` | procedure の再実行(ガイダンス)・script のサンドボックス |
| `test_rag_boundary.py` | `functions.py` が `rag_client` を import しない |
| `test_skill_extraction.py` | 抽出・ホールドアウトのゲート |
| `test_navigation.py` | 画面状態の推論(Phase 7) |
| `test_final_integration.py` | **偽のゲーム**での通し(目標 → TODO → ループ → 完了) |
| `test_gui_*.py` | 操作 GUI(FastAPI)・HUD(WebSocket)・認証・監査・管理画面の分離 |
| `test_docs.py` | この文書の検査 |

```bash
.venv/Scripts/pytest -q          # 全部
```

**型**: モデルは `ScriptedFoundationModelClient` に**期待する答えを順に登録**し、ゲームは**状態を持つ偽物**(`apply(action)` で状態が進む)にする。**決定的**なので、何度走らせても同じ結果になる。

## 5. 総合演習: 新しい API の入力を足す

**課題**: ゲームパッドの**スティックの傾け**(`stick_tilt(x, y, hold_ms)`)を、API の入力に足します。

1. **バックエンド**(`api_primitives.py`): `PadBackend` に、スティックの傾けを足す(`Protocol` と、`NullPadBackend`・`ViGEmPadBackend`)。
2. **`ApiPrimitives`** に、`stick_tilt` を足す(引数の範囲を**検査**: −1.0〜1.0)。
3. **許可リスト**(`dispatcher.py` の `_ALLOWED_API_PRIMITIVES`)に、`"stick_tilt"` を**足す**(足し忘れると、モデルが選んでも `KeyError`)。
4. **テスト**: 範囲外の値・許可リスト・Null バックエンドの記録。
5. プロンプトに、使える入力の一覧を**伝える**(モデルが、新しい入力を知らないと、選べない)。

```python
# 実行できる
from vlm_auto_replay.actions.api_primitives import ApiPrimitives, NullKeyboardMouseBackend, NullPadBackend, ScriptedOcrBackend


class StickApi(ApiPrimitives):
    """演習の途中経過: 検査つきの stick_tilt(まだバックエンドには届けない)。"""

    def __init__(self, *args):
        super().__init__(*args)
        self.sticks = []

    def stick_tilt(self, x: float, y: float, hold_ms: int) -> None:
        if not (-1.0 <= x <= 1.0 and -1.0 <= y <= 1.0):
            raise ValueError(f"スティックの傾きは -1.0〜1.0 です: ({x}, {y})")
        self.sticks.append((x, y, hold_ms))


api = StickApi(NullPadBackend(), NullKeyboardMouseBackend(), ScriptedOcrBackend())
api.stick_tilt(0.5, -1.0, 100)
print("記録:", api.sticks)
try:
    api.stick_tilt(1.5, 0.0, 100)
except ValueError as exc:
    print("範囲外:", exc)
```

```text
# 出力
記録: [(0.5, -1.0, 100)]
範囲外: スティックの傾きは -1.0〜1.0 です: (1.5, 0.0)
```

**考えること**: **範囲の検査は、どこに置く**のがよいか(`ApiPrimitives`・バックエンド・ディスパッチャ)。モデルが返した値は**信用しない**ので、**入口に近い所**で検査する。

### 自分で拡張する課題

1. サンドボックスの `_guarded_import` を、**許可リスト**(`math` だけ)に直し、第 4 回の 2 つの抜け道が止まることを、テストで確かめる。
2. `Watchdog` に、**3 つ目の条件**(直近 N ステップの画面の要約が、すべて同じ)を足す。誤検知を、どう抑えるか。
3. `accept_skill` を、**最初の失敗で止める**ように変える。長所と短所は(全部試す今の動きと比べて)。

## 確認問題

1. 自動生成のスキルを、ホールドアウトで試してから登録するのは、なぜですか。
2. ホールドアウトが空のとき、`all([])` が危ないのは、なぜですか。
3. RAG の結果のうち、`experience` から `summary` だけを渡すのは、なぜですか。
4. 偽のモデルと偽のゲームで、テストする利点は。
5. 新しい API の入力を足したとき、許可リストに足し忘れると、何が起きますか。

## 解答

1. 作った状況でだけ動くスキル(**過学習**)を、弾くため。生成に使わなかったシナリオで、**汎化**を確かめる。
2. `all` は、空なら**真**を返す。何も試していないのに「合格」になり、ゲートが**素通り**する。
3. `problem`・`betterWay` の全文は長く、**焦点がぼやける**(モデルの入力も増える)。要約で足りる。
4. **決定的**(何度でも同じ)・**速い**・本物の課金・ドライバが要らない。ループの全体を、安全に繰り返し確かめられる。
5. モデルがその入力を選んでも、`ActionDispatcher` が `KeyError`(未知の API primitive)で拒否する。

## まとめ

- スキルは**自動抽出**できるが、**ホールドアウトの受け入れゲート**を通してから登録する(空は、エラー)。Phase 6・7 は軽量実装で、反復が前提。
- RAG は `decompose_goal_with_rag` だけ。`docs` は本文、`experience` は要約だけを渡す。
- テストは、**偽のモデルと偽のゲーム**で決定的に。新しい入力は、**入口で検査し、許可リストに足す**。
- ここから先は、[学習資料](../learn/index.md)(保守・拡張の実務編)へ。
