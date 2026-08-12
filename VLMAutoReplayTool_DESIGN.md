# VLMAutoReplayTool 設計書

**設計指標: 基盤モデルによる計画と専用モデルによる実行を分離したゲーム自動プレイ**
作成日: 2026-08-11 / 想定規模: 大規模・一部R&D(agent loop+skill library+HID実行層+navigation localization)

本書はCEDEC講演(基盤モデルによるゲーム自動プレイ・エージェント構成)に触発された新規ツール構想を、実装可能な粒度まで具体化した設計書である。中核となる発想は「基盤モデルによる実行前の計画→たくさんある機能チェック(スキル)→専用モデルによる操作」という3段構成であり、動画リプレイ(録画済みログの後解析)とリアルタイム稼働(画面キャプチャ+仮想HIDによる実プレイ)の両方に対応する。知識ソースはドキュメント系(マニュアル・wiki・攻略本)と経験系(過去の対戦・失敗ログ)の2系統を持ち、スキルは攻撃・ジャンプ・回復・必殺技のようなゲームタイトル単位の操作手順として蓄積される。

メインループの骨格は一貫して単純である: 目標をTODOに分解→{ゲーム画面を観測→基盤モデルが次のアクションを決定→アクションを実行し進行を確認→最初に戻る}。各ステップで「何ステップ目か・AIの行動理由・実行したアクション・現在のTODO」を必ずログに残し、後から行動理由を追跡できる状態を保つ。

---

## Phase 0: コンセプト・要件定義

### 目的

「計画(基盤モデル)」と「実行(専用モデル・スキル・API)」を明確に分離したゲーム自動プレイエージェントを構築する。基盤モデルは高コスト・高レイテンシだが汎用的な推論(TODO分解・次アクション決定・診断)、専用モデル・スキル・APIは低レイテンシで再現性の高い操作実行を担当する。この分離により、モデルを差し替えてもループ形状・ログスキーマ・Skill/API分類が変わらないアーキテクチャを最初に固定する。

### 概念モデル

```
Action = Skill(procedure)      # ゲームタイトルごとに追加できる手順書。手動/自動生成
       | Skill(script)          # Pythonコード。UI操作系は自動生成
       | APICall(primitive)     # HID直接操作。pad入力/OCR/movementの定義済みプリミティブ
```

- **Skill**: 一連の操作・手順をまとめた手順書。ゲームタイトルごとに追加でき、追加経路は手動(人間が記述)と自動(Phase6の録画+入力ログからの抽出)の両方を許す。procedure(自然言語の手順書。アクションはあくまで参考であり従わなくてもよい)とscript(Pythonコード。UI操作系のスクリプトは自動生成される)の2種類に分かれる。
- **API**: パッド・マウス・キーボードの直接操作。システム固有で定義済みのPrimitiveな機能(Pad操作、OCR、movement)。Skillの実行基盤としても、基盤モデルが直接呼び出す最小単位としても使われる。

RAGはTODO作成時にのみ使用する。これはアーキテクチャ境界として強制する(Phase4参照)。プレイログはAIに渡して経験(Experience: Title/Summary/Problem/BetterWay)を抽出し、TODO作成時にはそのうちSummaryのみを付与する。

### 要求機能(9つのプロンプト意図をそのまま型として実装する)

ユーザー原文の9つの意図を意訳せず、そのままテンプレート関数として固定する(詳細はPhase1):

1. 「達成するために必要な作業をTODOリストに分解してください」→ `decompose_goal_to_todo`
2. 「なぜそのアクションにしたのか、簡潔に説明して」→ `explain_action_choice`
3. 「Goalを達成するために次のアクションを生成してください」→ `generate_next_action`
4. 「再利用可能な高価値の知見(落とし穴、操作ルール、前提条件)を抽出して」→ `extract_experience`
5. 「画面上に観測できる変化だけを1〜2文で要約して」→ `summarize_screen_change`
6. 「重複操作をまとめて、差分をParameterにして1つに統合して」→ `merge_duplicate_operations`
7. 「動画と手順書から良い感じにPythonコード作って」→ `generate_code_from_video_and_procedure`
8. 「再利用可能サブルーチンとして自動的に切り出してください」→ `extract_reusable_subroutine`
9. 「処理が停滞している原因を、分析し、次に試すべきアプローチを特定して」→ `diagnose_stall`

### 非機能要件

- **レイテンシ予算**: 基盤モデル呼び出しはメインループのクリティカルパスに乗る。ステップあたりのレイテンシ目標値・計測方法は別文書「ProfilingTool設計書」へ前方参照する(本書はStepLogスキーマを計装対象として提供する側)。
- **監査可能性**: 行動前に必ずreasoningをログする。基盤モデルが「なぜそのアクションを選んだか」を残さずアクションを実行することを禁止する(Phase2のStepLog必須フィールド)。
- **差し替え可能性**: 基盤モデル(計画側)・専用モデル(実行側)はインターフェース越しに差し替え可能とする。特定ベンダー・特定モデル名をループ本体のロジックに直接埋め込まない。

### agent-loopの実在する先行研究(設計判断の裏付け)

- **ReAct**(観察→思考→行動の反復): メインループ`capture→generate_next_action→execute→repeat`の直接の先例。
- **Plan-and-Execute**(目標分解→実行): `decompose_goal_to_todo`でTODOへ分解してから`generate_next_action`で個々のステップを実行する二層構造の先例。
- **Voyager**(Minecraft LLMエージェント): コード化スキルライブラリを自動抽出しながら拡張する発想の直接の先行事例。Phase6のscriptスキル自動生成で参照する。
- **Reflexion**(過去の失敗ログからの自己内省): `diagnose_stall`が「現在のTODOの過去試行ログ」から次のアプローチを特定する設計の裏付け。

### 前提・制約

- RAGはTODO生成時のみ使用する(`decompose_goal_to_todo`のみがRAGクライアントを持つ)。これはPhase4でアーキテクチャ境界として強制する。
- 画面キャプチャのインターフェース設計は、`DevelopmentRAGEnvironment`の`screen_capture.py`(Houdiniビューポート/ネットワークエディタのスクリーンショット取得。`capture_viewport()`/`capture_viewport_clip()`は実機検証済み)のflipbook/frame-grab形を前例として引用する。実装自体は別物(ゲーム画面用)だが、「画面キャプチャ→VLM」という入力形状が同型である点は`DevelopmentRAGEnvironment\IMPROVEMENT_PLAN.md`のPhase2でも指摘されており、共有キャプチャサービスインターフェースの抽出余地がある。
- RAGサービスは新規に構築せず、`DevelopmentRAGEnvironment`の`rag_local_bridge.py`(既定ポート`:8766`、`X-API-Key`ヘッダ認証)をそのまま再利用する。

### アンチパターン(全フェーズ共通)

- 新規RAGサービスを構築しない。`:8766`ブリッジの認証方式・API契約を変更せずそのまま利用する。
- `generate_next_action`にRAGクライアント参照を持たせない(TODO生成時限定という境界を破らない)。
- 一時的な失敗を即座にTODO再構築のトリガーにしない(Phase5参照)。過剰反応するウォッチドッグにしない。
- procedureスキルの手順を強制コンプライアンス化しない(逸脱を許すことがこのツールの核心的判断)。
- Phase6/Phase7で「一度で完成させる」前提の見積もりをしない(後述の優先度注記を参照)。

**検証チェックリスト:**
- [ ] `Action = Skill(procedure|script) | APICall(primitive)`の分類がPhase3のディレクトリ構成に一致している
- [ ] 9つのプロンプト意図それぞれに対応する関数名がPhase1のテンプレート層で1対1に存在する
- [ ] RAGクライアントを保持するモジュールが`decompose_goal_to_todo`実装のみであることをコードレビューで確認できる
- [ ] `screen_capture.py`との相互参照(インターフェース前例)が本書とDevelopmentRAGEnvironment側の両方に記載されている
- [ ] 先行研究(ReAct/Plan-and-Execute/Voyager/Reflexion)とPhaseの対応関係が本書内で追跡できる

---

## Phase 1: プロンプトテンプレート層(最優先・9意図を型として固定)

**目的:** 9つのプロンプト意図を、固定入出力スキーマを持つ型付きテンプレート関数として実装する。モデルを差し替えても呼び出し側(MainLoop・SkillRunner・Watchdog)のコードを変更しなくて済むようにする。

**実装内容:**

各関数はPydanticモデルで入出力を固定し、プロンプト文字列の組み立てと基盤モデル呼び出しをこの層に閉じ込める。

```python
# prompts/schemas.py
from pydantic import BaseModel
from typing import Literal

class TodoItem(BaseModel):
    todoId: str
    description: str
    doneCriteria: str          # 完了判定に使う観測可能な条件
class DecomposeGoalOutput(BaseModel):
    todos: list[TodoItem]
    ragContextUsed: list[str]  # 参照した知識ソースのID(監査用)
class ExplainActionOutput(BaseModel):
    reasoning: str             # 簡潔な説明(1〜3文)
class NextActionOutput(BaseModel):
    actionType: Literal["skill", "api"]
    actionId: str
    params: dict
class ExperienceOutput(BaseModel):
    title: str; summary: str; problem: str; betterWay: str
class ScreenChangeSummary(BaseModel):
    summary: str                # 観測できる変化のみ、1〜2文
class MergedOperation(BaseModel):
    mergedSkillId: str
    paramSchema: dict           # 差分をParameter化したスキーマ
class GeneratedCode(BaseModel):
    scriptCode: str
    sourceTrace: list[str]      # 元にした動画フレーム/手順書箇所の参照
class ExtractedSubroutine(BaseModel):
    subroutineId: str; occurrenceCount: int; candidateActions: list[str]
class StallDiagnosis(BaseModel):
    rootCause: str; nextApproach: str; shouldRebuildTodo: bool
```

9つの関数はすべて `def fn(input_schema) -> output_schema` の形でモデル呼び出しをラップする。呼び出し側は戻り値の型だけを見ればよい。

```python
# prompts/functions.py
def decompose_goal_to_todo(goal: str, rag_context: list[str]) -> DecomposeGoalOutput: ...
def explain_action_choice(action: NextActionOutput, context: dict) -> ExplainActionOutput: ...
def generate_next_action(todo: TodoItem, screen_obs: bytes, history: list[dict]) -> NextActionOutput: ...
def extract_experience(play_log: list[dict]) -> ExperienceOutput: ...
def summarize_screen_change(before: bytes, after: bytes) -> ScreenChangeSummary: ...
def merge_duplicate_operations(candidate_ops: list[dict]) -> MergedOperation: ...
def generate_code_from_video_and_procedure(video_ref: str, procedure_text: str) -> GeneratedCode: ...
def extract_reusable_subroutine(session_logs: list[dict]) -> list[ExtractedSubroutine]: ...
def diagnose_stall(current_todo: TodoItem, todo_attempt_log: list[dict]) -> StallDiagnosis: ...
```

各関数名・シグネチャはユーザー原文の意図に1対1対応させ、後続フェーズ(MainLoop・SkillRunner・Watchdog・SkillExtractor)はこの9関数のみに依存する。モデルプロバイダを切り替える際の変更範囲をこの層に限定するのが狙い。

**検証チェックリスト:**
- [ ] 9関数すべてが固定入出力スキーマ(Pydantic)を持ち、`dict`の生渡しをしていない
- [ ] `generate_next_action`のシグネチャがRAGコンテキストを一切受け取らない(Phase4の境界と整合)
- [ ] `decompose_goal_to_todo`の出力に`ragContextUsed`(監査用の参照ID列)が含まれる
- [ ] モデル呼び出し実装を差し替えても呼び出し側コードに変更が不要であることをモック実装で確認する
- [ ] 9関数それぞれがPhase0の9意図と1対1で対応表になっている(命名の意訳がない)

---

## Phase 2: メインループ+行動ログ(コアエンジン)

**目的:** `capture→generate_next_action→log_step→execute→状態進行確認→repeat`という単純なループを、後から行動理由を必ず追跡できる形で実装する。

**実装内容:**

```python
# loop/main_loop.py
class MainLoop:
    def run(self, todo: TodoItem, max_steps: int) -> list[StepLog]:
        logs = []
        for step_index in range(max_steps):
            observation = self.capture()  # 画面キャプチャ(前例: screen_capture.py)
            next_action = generate_next_action(todo, observation, self._recent_history(logs))
            reasoning = explain_action_choice(next_action, {"todo": todo, "step": step_index})

            log = StepLog(
                stepIndex=step_index,
                timestamp=self._now(),
                todoId=todo.todoId,
                observationRef=self._store_observation(observation),
                reasoning=reasoning.reasoning,
                actionTaken={"type": next_action.actionType, "id": next_action.actionId, "params": next_action.params},
                resultObservationSummary=None,  # execute後に埋める
            )

            before = observation
            self.execute(next_action)
            after = self.capture()
            log.resultObservationSummary = summarize_screen_change(before, after).summary

            logs.append(log)
            self.log_step(log)  # 永続化(監査可能性の要件を満たす)

            if self.watchdog.should_intervene(todo, logs):  # Phase5
                break
            if self._todo_done(todo, after):
                break
        return logs
```

**StepLogスキーマ**(別文書「ProfilingTool設計書」の直接の計装対象):

```python
# loop/schemas.py
class StepLog(BaseModel):
    stepIndex: int
    timestamp: str                  # ISO8601
    todoId: str
    observationRef: str             # 画面キャプチャの保存先参照(パス/ID)
    reasoning: str                  # explain_action_choiceの出力
    actionTaken: dict               # {"type": "skill"|"api", "id": str, "params": dict}
    resultObservationSummary: str   # summarize_screen_changeの出力
```

`resultObservationSummary`はPhase1の`summarize_screen_change`が生成する。固定ディレイでの前進ではなく、この要約をもとに状態が実際に変化したかを確認してからループを進める(Final Phaseの検証項目と対応)。

**検証チェックリスト:**
- [ ] `StepLog`の全フィールドが毎ステップ非nullで埋まる(`resultObservationSummary`はexecute後に必ず埋まる)
- [ ] `reasoning`が空文字列のままアクションが実行されるケースがないことをアサーションで保証する
- [ ] `log_step`がループ本体と非同期・同期いずれの構成でも欠損なく永続化される
- [ ] `resultObservationSummary`をもとにした状態進行確認が固定ディレイ実装に依存していない
- [ ] `stepIndex`が単調増加し、Watchdog介入時にも欠番・重複が発生しない

---

## Phase 3: Action実行層(Skill/API二層+HID)

**目的:** Phase0で固定した`Action = Skill | APICall`の分類を実行可能な層として実装する。Windows環境であることを踏まえHID実装技術を名指しで選定する。

**実装内容:**

```python
# actions/api_primitives.py
# Windows HID実装: 仮想コントローラは ViGEm(仮想XInput/DS4)、キーボード・マウスは SendInput 系フックを用いる
class ApiPrimitives:
    def pad_input(self, button: str, hold_ms: int) -> None: ...      # ViGEm経由
    def key_input(self, key: str, hold_ms: int) -> None: ...          # SendInput経由
    def mouse_move(self, dx: int, dy: int) -> None: ...               # SendInput経由
    def ocr(self, region: tuple[int, int, int, int]) -> str: ...
    def movement(self, direction: str, duration_ms: int) -> None: ...

# actions/skill.py
class Skill(BaseModel):
    skillId: str
    gameTitle: str
    type: Literal["procedure", "script"]
    proceduralText: str | None = None   # type=="procedure"のみ
    scriptCode: str | None = None       # type=="script"のみ
    paramSchema: dict
    createdBy: Literal["manual", "auto"]
    sourceTrace: list[str] | None = None  # 自動生成時の元ソース参照(動画/ログ)

# actions/skill_runner.py
class SkillRunner:
    def run_procedure_skill(self, skill: Skill, todo: TodoItem, loop: MainLoop) -> list[StepLog]:
        # procedureスキルは別実行パスを持たない。
        # 手順テキストを非強制ガイダンスとしてMainLoopへ注入し、同じループを再実行する。
        # generate_next_actionの出力が手順から逸脱しても止めない(非強制性の実証はFinal Phase)。
        return loop.run(todo, max_steps=..., guidance_text=skill.proceduralText)

    def run_script_skill(self, skill: Skill, params: dict) -> None:
        # scriptスキルはサンドボックス化されたPython直接実行
        self._sandboxed_exec(skill.scriptCode, params)
```

procedureスキルの実行は「TODO+アクション(答え)を使いメインループを答えつきで動かす」形と整理される。ガイダンステキストは`generate_next_action`のプロンプトに参考情報として渡るのみで、従わなければならない制約にはしない。scriptスキルはサンドボックス化されたPython実行環境で直接実行され、UI操作系スクリプトはPhase6で自動生成される。

**検証チェックリスト:**
- [ ] `pad_input`/`key_input`/`mouse_move`がそれぞれViGEm/SendInput経由で実際にHIDイベントを発生させることを最小テストで確認する
- [ ] procedureスキル実行が`SkillRunner`内で独立した別ループを持たず、`MainLoop.run`を再利用していることをコードで確認する
- [ ] scriptスキル実行がサンドボックス外のファイル/ネットワークアクセスを行わないことを確認する
- [ ] `Skill`スキーマの`type`が`procedure`/`script`いずれの場合も対応するフィールドのみ非nullである
- [ ] procedureスキルのガイダンスから逸脱した`generate_next_action`出力が実行を妨げられないこと(非強制性)

---

## Phase 4: 知識ソース+RAG(TODO生成時限定)

**目的:** ドキュメント知識(マニュアル/wiki/攻略)と経験知識(過去試合/失敗)を、`DevelopmentRAGEnvironment`の既存RAGブリッジ上に構築する。新規RAGサービスは作らない。

**実装内容:**

```python
# knowledge/rag_client.py
# DevelopmentRAGEnvironment の rag_local_bridge.py (:8766, X-API-Key) をそのまま再利用する
import requests

class VLMReplayRagClient:
    BASE_URL = "http://localhost:8766"

    def __init__(self, api_key: str):
        self._headers = {"X-API-Key": api_key}

    def search(self, query: str, namespace: Literal["docs", "experience"], limit: int = 6) -> dict:
        # docs / experience を別namespaceとして同一ブリッジ上に実装する
        resp = requests.post(
            f"{self.BASE_URL}/search",
            headers=self._headers,
            json={"query": query, "limit": limit, "namespaces": [namespace]},
        )
        return resp.json()
```

`docs`namespaceにはマニュアル・wiki・攻略本を投入する。`experience`namespaceには、プレイログから`extract_experience`(Phase1)が抽出した`Experience{Title, Summary, Problem, BetterWay}`を投入するが、将来のTODO生成に添付するのは**Summaryのみ**であり、Problem/BetterWay全体を都度添付することはしない(ユーザー原文の厳密な指定)。

```python
# knowledge/experience.py
class Experience(BaseModel):
    title: str
    summary: str      # decompose_goal_to_todoに添付されるのはこのフィールドのみ
    problem: str
    betterWay: str

# RAG呼び出しの注入先は decompose_goal_to_todo のみに限定する。
# generate_next_action は RAGクライアントの参照を一切持たないよう、
# モジュール分割時点で依存を切る(import自体をしない)ことでアーキテクチャレベルで強制する。
def decompose_goal_to_todo(goal: str, rag_client: VLMReplayRagClient) -> DecomposeGoalOutput:
    docs = rag_client.search(goal, namespace="docs")
    exp = rag_client.search(goal, namespace="experience")  # Summaryのみ利用
    ...

def generate_next_action(todo: TodoItem, screen_obs: bytes, history: list[dict]) -> NextActionOutput:
    # rag_client を引数に持たない。ステップ実行中にRAGは一切呼び出されない
    ...
```

**検証チェックリスト:**
- [ ] `docs`/`experience`が同一`:8766`ブリッジ上の別namespaceとして実装され、新規RAGサービスが立ち上がっていない
- [ ] `generate_next_action`のシグネチャ・実装のどちらにもRAGクライアントのimportが存在しない(静的解析で確認)
- [ ] TODO生成に添付される`Experience`が`summary`フィールドのみであり、`problem`/`betterWay`全体が渡っていない
- [ ] `X-API-Key`ヘッダ認証が本ツールのRAG呼び出しでも維持されている
- [ ] 1ステップ実行中(`generate_next_action`〜`execute`)のRAG呼び出し回数が0回であることをコールカウントで確認できる(Final Phaseで実証)

---

## Phase 5: スタック検知+Watchdog+TODO再構築(頑健性)

**目的:** はまった状態を許容しつつ、回復不能な停滞だけを検知してTODOを再構築する。過剰反応も無反応も避ける。

**実装内容:**

一時的な失敗は許容する(即座に再構築しない)。再構築トリガーは以下の**いずれか**が成立した場合のみ発火する(二重条件、ユーザー原文通り):

1. `diagnose_stall`が「回復不能」と判断した場合(`StallDiagnosis.shouldRebuildTodo == True`)
2. 同一TODOがNステップ以上継続した場合(強制発動)

```python
# loop/watchdog.py
class Watchdog:
    def __init__(self, stall_step_threshold: int = 15):
        self._threshold = stall_step_threshold

    def should_intervene(self, todo: TodoItem, logs: list[StepLog]) -> bool:
        same_todo_logs = [l for l in logs if l.todoId == todo.todoId]

        # 条件2: 同一TODOがNステップ以上継続(強制発動)
        if len(same_todo_logs) >= self._threshold:
            return True

        # 条件1: diagnose_stallが回復不能と判断
        diagnosis = diagnose_stall(todo, self._as_attempt_log(same_todo_logs))
        return diagnosis.shouldRebuildTodo

    def rebuild_todo(self, current_todos: list[TodoItem], current_todo: TodoItem, logs: list[StepLog]) -> list[TodoItem]:
        # 再構築の入力は「現在のTODOリスト + そのTODOの過去試行ログ」のみ。
        # セッション全体ログは渡さない(スコープの厳密化)。
        attempt_log = [l for l in logs if l.todoId == current_todo.todoId]
        return decompose_goal_to_todo(
            goal=self._reconstruct_goal(current_todos, current_todo),
            rag_context=attempt_log,  # 現在TODOの過去ログのみ
        ).todos
```

`diagnose_stall`はPhase1で定義した`StallDiagnosis{rootCause, nextApproach, shouldRebuildTodo}`を返す。ウォッチドッグはこの判定結果と、同一TODO継続ステップ数のカウントの2系統を独立に監視し、どちらかが閾値・条件を満たした瞬間に再構築フローへ入る。

**検証チェックリスト:**
- [ ] 一時的失敗フィクスチャ(数ステップで自然回復するケース)では再構築が発火しないこと
- [ ] 同一TODOが閾値Nステップに達した場合、`diagnose_stall`の判定に関わらず強制的に再構築が発火すること
- [ ] `diagnose_stall`が`shouldRebuildTodo=True`を返した場合、閾値未満でも再構築が発火すること
- [ ] 再構築時の入力に現在TODO以外(他TODOの過去ログやセッション全体ログ)が混入していないこと
- [ ] 再構築後の新TODOリストが`decompose_goal_to_todo`の固定出力スキーマ(`DecomposeGoalOutput`)に準拠していること

---

## Phase 6: スキル自動抽出(録画+入力ログ→Skill)(自己拡張)

**目的:** プレイ動画とパッド入力・キーボード入力のログから、procedureスキル・scriptスキルの両方を自動生成し、既存のスキルライブラリを拡張する。Voyagerのコード化スキルライブラリを直接の先行事例とする。

**実装内容:**

1. **重複操作の統合(procedureスキル抽出)**: `merge_duplicate_operations`(Phase1)が、類似する操作系列を検出し、差分をParameter化して1つのスキルへ統合する。

```python
# skills/extraction.py
def extract_procedure_skill(candidate_logs: list[list[StepLog]]) -> Skill:
    merged = merge_duplicate_operations([_as_op_seq(l) for l in candidate_logs])
    return Skill(
        skillId=merged.mergedSkillId,
        gameTitle=..., type="procedure",
        proceduralText=_render_procedure_text(merged),
        paramSchema=merged.paramSchema,
        createdBy="auto",
        sourceTrace=[l[0].observationRef for l in candidate_logs],
    )
```

2. **スクリプトスキル生成**: `generate_code_from_video_and_procedure`(Phase1)が動画+手順書からPythonコードを直接生成する。**Voyagerのコード化スキルライブラリを直接の先行事例として引用する**——実行可能コードとして蓄積し後続タスクから呼び出す発想がVoyagerの手法と一致する。

```python
# skills/extraction.py(続き)
def extract_script_skill(video_ref: str, procedure_text: str) -> Skill:
    generated = generate_code_from_video_and_procedure(video_ref, procedure_text)
    return Skill(
        skillId=_new_id(), gameTitle=..., type="script",
        scriptCode=generated.scriptCode,
        paramSchema=_infer_param_schema(generated.scriptCode),
        createdBy="auto",
        sourceTrace=generated.sourceTrace,
    )
```

3. **再利用可能サブルーチンの定期抽出**: `extract_reusable_subroutine`をセッション横断で定期実行し、繰り返し出現する部分系列を検出したら、頻度が閾値を超えたものをスキルへ昇格する。

4. **受け入れゲート**: 新規スキルは即時追加せず、ホールドアウトのreplayテストまたはliveテストでの受け入れゲートを通過させてから、スキルライブラリへ登録する。

```python
# skills/gate.py
def accept_skill(candidate: Skill, holdout_scenarios: list[TodoItem]) -> bool:
    results = [_replay_test(candidate, s) for s in holdout_scenarios]
    return all(r.succeeded for r in results)
```

**検証チェックリスト:**
- [ ] 明らかな3ステップ反復フィクスチャから正確に1つのマージ済みSkillが生成される(Final Phaseと対応する単体レベルの確認)
- [ ] 自動生成された`Skill`の`createdBy`が常に`"auto"`かつ`sourceTrace`が非空である
- [ ] 受け入れゲートを通過しないスキル候補がスキルライブラリへ追加されないこと
- [ ] `extract_reusable_subroutine`のセッション横断実行がメインループの実行時間をブロックしない(バックグラウンド/バッチ実行)
- [ ] scriptスキル生成の出力がPhase3の`SkillRunner.run_script_skill`が期待するサンドボックス実行形式と一致する

---

## Phase 7: ナビゲーション推論(視覚的自己位置推定パイプライン)

**目的:** 自動リプレイ中に「今どこにいるか」を画面キャプチャのみから推定する。ユーザー原文通り5段のパイプラインとして構成する。

**実装内容:**

```
capture → coarse search → scoring → localization → final confirmation
```

1. **capture**: 現在の画面キャプチャを取得する(Phase2の`MainLoop.capture()`と同一経路)。
2. **coarse search**: 既知の`ScreenState`群に対する**global descriptor近傍検索**(画像検索の粗段で一般的な手法)を行い、候補となる状態を絞り込む。
3. **scoring**: 絞り込んだ候補に対する**re-ranking段**として、局所特徴マッチング/テンプレートマッチ、または切り出し領域へのVLM再照会によりスコアを再計算する。
4. **localization**: スコア最上位の`ScreenState`を暫定的な現在位置として確定する。
5. **final confirmation**: OCRランドマークまたは特徴領域チェックサムによる独立検証を行い、誤確定を防ぐ安全ゲートとする。もっともらしいが誤った候補をここで棄却する。

```python
# navigation/screen_state.py
class ScreenState(BaseModel):
    stateId: str
    referenceImageRef: str
    ocrLandmarks: list[str]           # final confirmationで照合するテキストランドマーク
    knownTransitions: list[str]       # 遷移可能な次stateIdのリスト(状態遷移グラフ)

# navigation/localizer.py
class Localizer:
    def localize(self, observation: bytes, known_states: list[ScreenState]) -> ScreenState | None:
        coarse_candidates = self._global_descriptor_search(observation, known_states)  # 1. coarse search
        scored = self._rerank(observation, coarse_candidates)                          # 2. scoring
        best = scored[0] if scored else None                                            # 3. localization
        if best is None:
            return None
        if not self._final_confirm(observation, best):                                 # 4. final confirmation
            return None  # もっともらしいが誤った候補を棄却
        return best
```

ナビゲーションは`ScreenState`を状態遷移グラフのノードとして定義し、`knownTransitions`によって「今の状態から到達しうる次状態」を制約できる形にする。これにより`coarse search`の探索範囲を現在状態の近傍に絞ることも可能になる(実装最適化としては後続反復で検討)。

**検証チェックリスト:**
- [ ] coarse search→scoring→localization→final confirmationの4段が独立した関数として分離されている(1つの巨大関数に埋め込まれていない)
- [ ] final confirmationが「もっともらしいが誤った」候補を正しく棄却するテストケースが存在する(偽陽性防御)
- [ ] `ScreenState.knownTransitions`に存在しない遷移が発生した場合に検知できる(状態遷移グラフの整合性)
- [ ] OCRランドマーク照合がPhase3の`ApiPrimitives.ocr()`を再利用している(独自OCR実装を新設していない)
- [ ] coarse search段の候補数がscoring段の計算量を線形に抑える範囲に収まっている(全状態総当たりになっていない)

---

## Final Phase: 統合検証

- [ ] 決定的フェイクゲーム(入力に対する応答が固定・再現可能なテスト用ゲーム実装)でNステップ完走し、スキーマ妥当な`StepLog`列が生成されること
- [ ] 固定ディレイではなく、`resultObservationSummary`による状態変化確認後にのみループが進むこと
- [ ] procedureスキルが`generate_next_action`の出力を観測可能に変化させつつも、逸脱も許容されること(非強制性の実証)
- [ ] ステップ内(`generate_next_action`〜`execute`の1サイクル)でのRAG呼び出しが0回であることをコールカウントで静的に確認できること(Phase4のアーキテクチャ強制の実証)
- [ ] 一時的失敗フィクスチャでは再構築が発火しないこと(Phase5の許容動作の実証)
- [ ] 明らかな3ステップ反復フィクスチャから正確に1つのマージ済みSkillが生成されること(Phase6の実証)
- [ ] final confirmationが「もっともらしいが誤った」候補を正しく棄却すること(Phase7の偽陽性防御テスト)

---

## 相互参照セクション

- **RAGサービス**: 本ツールのRAGサービスは新規構築せず、`DevelopmentRAGEnvironment`の`rag_local_bridge.py`(`:8766`、`X-API-Key`ヘッダ認証)を`docs`/`experience`の2namespaceで直接再利用する。同文書のPhase2(VLM対応)でも本ツールとの相互参照が明記されている。
- **画面キャプチャ**: `DevelopmentRAGEnvironment`の`screen_capture.py`(`capture_viewport()`/`capture_viewport_clip()`)を、画面キャプチャインターフェースの前例として引用する。両ツールの「画面キャプチャ→VLM」という入力形状の類似性はユーザー自身が指摘済みであり、共有キャプチャサービスインターフェースの抽出を将来的な推奨事項として明記する。
- **ProfilingTool設計書**: 本書の`StepLog`スキーマを直接の計装対象とする。ステップあたりレイテンシ予算(Phase0で前方参照した非機能要件)の実測は同文書側で扱う。
- **VisualRegressionQATool設計書**: 本ツールのHID/入力注入層(Phase3の`ApiPrimitives`、ViGEm/SendInput実装)は、同文書の`InputPlaybackDriver`の将来バックエンド候補となることを明記する。

---

## 優先度注記

別文書「DynamicGIMiddleware設計書」と並び、本バッチ中最もR&D色が強い文書である。アーキテクチャ(ループ形状・スキーマ・Skill/APIの分類)は現時点で確定的にコミットできるため具体的に設計したが、基盤モデル・専用モデルの選定と精度目標(ナビゲーション推定精度、スキル抽出の再現率等)は明示的にヘッジし、実装反復の中で確定させる方針とする。特にPhase6(スキル自動抽出)とPhase7(視覚的自己位置推定)は、他フェーズと異なり一発の実装では収束しにくく、複数回の反復(パラメータ調整・受け入れゲートの閾値調整・re-ranking手法の見直し)が前提になりやすい点をあらかじめ明記しておく。
