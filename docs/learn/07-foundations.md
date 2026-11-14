# 7. 基礎の補強(言語・設計思想・アルゴリズム・用語)

講義と学習資料の**前提になっている知識**を、ここで補います。「Python・型ヒントの基本」を、**このツールのコードで実際に使われている形**で、動かして確かめます。

## 1. 言語の基礎: VLM で使う Python

### 1.1 型ヒント・`Literal`・`X | None`

型ヒントは**実行には影響しない注釈**。`Literal["procedure", "script"]` は、「**この 2 つの文字列のどちらか**」。`str | None` は「文字列か、無いこと」。**取りうる値を型で絞る**と、間違いを早く見つけられる。

```python
# 実行できる
from typing import Literal


def make_skill_kind(kind: Literal["procedure", "script"]) -> str:
    return f"種類: {kind}"


print(make_skill_kind("procedure"))
# 型検査ツール(mypy など)は、make_skill_kind("macro") を実行前に警告する。実行自体は、止まらない
print(make_skill_kind("macro"))
```

```text
# 出力
種類: procedure
種類: macro
```

- **型ヒントだけでは、実行時に守られない**(最後の行)。**実行時に守る**には、次の pydantic を使う。

### 1.2 pydantic: 実行時の検証

`BaseModel` を継ぐと、**作るとき(`Skill(...)`)に、型と規則を検査**する。`@model_validator` で、**項目どうしの関係**(「`procedure` なら `proceduralText` が必須」)も守れる。**壊れたデータを、後ろの処理に通さない**(入口で止める)。

```python
# 実行できる
from pydantic import ValidationError

from vlm_auto_replay.actions.skill import Skill

ok = Skill(skillId="s1", gameTitle="demo", type="procedure", proceduralText="1. 開く", paramSchema={}, createdBy="manual")
print("作れた:", ok.skillId, ok.type)

for label, kwargs in [
    ("procedure なのに手順書が無い", dict(type="procedure", createdBy="manual")),
    ("自動生成なのに出どころが無い", dict(type="procedure", proceduralText="x", createdBy="auto")),
    ("script なのに両方ある", dict(type="script", scriptCode="pass", proceduralText="x", createdBy="manual")),
]:
    try:
        Skill(skillId="s", gameTitle="g", paramSchema={}, **kwargs)
        print(label, "→ 通ってしまった")
    except ValidationError as error:
        print(label, "→ 拒否:", error.errors()[0]["type"])
```

```text
# 出力
作れた: s1 procedure
procedure なのに手順書が無い → 拒否: value_error
自動生成なのに出どころが無い → 拒否: value_error
script なのに両方ある → 拒否: value_error
```

### 1.3 `Protocol`: 差し替えられる口

`PadBackend`・`KeyboardMouseBackend`・`OcrBackend` は、「**こういうメソッドを持つもの**」という約束(`Protocol`)。**本物のコントローラー**でも、**テスト用の偽物**でも、同じ呼び出しで動く。**実機がなくてもテストできる**のは、この作りのおかげ。

```python
# 実行できる
from typing import Protocol


class PadBackend(Protocol):
    def press(self, button: str) -> None: ...


class FakePad:                        # テスト用の偽物。押したボタンを記録するだけ
    def __init__(self):
        self.pressed = []

    def press(self, button: str) -> None:
        self.pressed.append(button)


def run_steps(pad: PadBackend, steps: list[str]) -> None:
    for button in steps:
        pad.press(button)             # 本物か偽物かは、知らない


pad = FakePad()
run_steps(pad, ["A", "B", "START"])
print("記録:", pad.pressed)
```

```text
# 出力
記録: ['A', 'B', 'START']
```

### 1.4 リスト内包・`in`・集合

```python
# 実行できる
logs = [
    {"todoId": "t1", "ok": False}, {"todoId": "t1", "ok": False},
    {"todoId": "t2", "ok": True}, {"todoId": "t1", "ok": False},
]
same = [log for log in logs if log["todoId"] == "t1"]            # リスト内包: 条件に合うものだけ
print("t1 の試行回数:", len(same))
print("使われた TODO:", sorted({log["todoId"] for log in logs}))   # 集合内包: 重複なし
print("失敗が 1 つでもある:", any(not log["ok"] for log in logs), " 全部成功:", all(log["ok"] for log in logs))
```

```text
# 出力
t1 の試行回数: 3
使われた TODO: ['t1', 't2']
失敗が 1 つでもある: True  全部成功: False
```

## 2. 設計思想

| 原則 | 意味 | VLM のどこに出るか | 破るとどうなるか |
|---|---|---|---|
| **計画と実行を分ける** | 「何をするか決める(VLM)」と「実際に操作する(API)」を別の層にする | プロンプト層 / ループ / 実行層 | 判断の誤りが、そのまま実機の操作になる |
| **入口で検証する** | 外から来るデータは、型と規則で止める | pydantic(`Skill`・`TodoItem`) | 壊れたデータが、奥で謎の失敗になる |
| **差し替えられる口(Protocol)** | 実機・OCR を、偽物と交換できる | `actions/api_primitives.py` | 実機が無いとテストできない |
| **一時的な失敗は許す** | 1 回の失敗で、すぐ作り直さない | `Watchdog`(しきい値まで待つ) | 揺れるたびに計画が壊れる |
| **二重の条件で介入する** | 「回復不能」か「N ステップ続いた」のどちらか | `should_intervene` | 無限に同じことを繰り返す |
| **スコープを絞る** | 再構築に渡すのは、**そのタスクの過去ログだけ** | `rebuild_todo` | 無関係な文脈で、判断がぶれる |
| **多層防御(完全な境界ではない)** | 1 つの防御に頼らない。限界を書く | `sandbox.py`(コメントに限界を明記) | 破られたとき、全部が破られる |

## 3. 関数・アルゴリズム

| 名前 | 場所 | 何をする | 計算量・注意 |
|---|---|---|---|
| **観測 → 判断 → 実行のループ** | `loop/main_loop.py` | 撮る → VLM に聞く → 操作 → 繰り返す。**上限のステップ数**を持つ | O(ステップ数)。**必ず終わる**上限が要る |
| **スタック検知** | `loop/watchdog.py` | 同じ TODO のログ数が、しきい値(既定 15)以上なら強制介入。そうでなければ診断を聞く | O(ログ数) |
| **TODO の再構築** | `Watchdog.rebuild_todo` | 現在の TODO と過去ログだけから、計画を作り直す | VLM の呼び出し回数に依存 |
| **制限つきの `exec`** | `actions/sandbox.py` | 許可した組み込み関数だけを渡し、`import` を見張る | O(コード長)。**ブロックリスト方式は、抜けがあれば破られる** |

```python
# 実行できる
import ast

code = """
import os
from subprocess import run
x = 1 + 2
"""
tree = ast.parse(code)                                          # コードを「木」にする(実行はしない)
imported = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported += [alias.name.split(".")[0] for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        imported.append((node.module or "").split(".")[0])
blocked = {"os", "subprocess", "socket"}
print("import しているモジュール:", imported)
print("禁止リストに当たる:", sorted(set(imported) & blocked))

# ブロックリストの限界: 禁止していない別の道がある、と 1 つでも残ると破れる
print("禁止リストに無い:", "builtins" not in blocked)
```

```text
# 出力
import しているモジュール: ['os', 'subprocess']
禁止リストに当たる: ['os', 'subprocess']
禁止リストに無い: True
```

- **許可リスト(これだけ使える)**の方が、**ブロックリスト(これは使えない)**より安全。いまの実装は、組み込み関数は許可リスト式だが、**`import` はブロックリスト式**(抜けがあれば破られる。→ 既知の問題)。**本当に信頼できないコードは、OS の分離(コンテナ・別プロセス)で実行する**。

## 4. IT・エンタメの用語の補強

| 用語 | 意味 | VLM での出番 |
|---|---|---|
| **VLM(Vision-Language Model)** | 画像と文章を一緒に理解する AI | 画面を見て、次の操作を決める |
| **OCR** | 画像の中の文字を、文字データにする | `OcrBackend` |
| **プロンプト** | AI に渡す指示文 | `prompts/` |
| **TODO リスト(タスク分解)** | 大きな目標を、小さな手順に分ける | `decompose_goal_to_todo` |
| **スキル(手順書 / スクリプト)** | 繰り返し使える操作のまとまり | `Skill`(`procedure` / `script`) |
| **HID(ヒューマン・インターフェース・デバイス)** | キーボード・マウス・ゲームパッドなどの入力機器 | `PadBackend`・`KeyboardMouseBackend` |
| **ウォッチドッグ(番犬)** | 進まなくなったことを見つけて、介入する仕組み | `Watchdog` |
| **サンドボックス** | 実行できることを制限した、囲いの中 | `ScriptSandbox` |
| **スタック(行き詰まり)** | 同じ状態から、進めなくなること | スタック検知 |
| **QA 自動化 / 自動プレイ** | 人の代わりに、ゲームを操作してテストする | 全体の目的 |
| **ゲームパッド / 入力のエミュレーション** | 機器の入力を、プログラムで再現する | `PadBackend` |
| **FastAPI / pydantic** | Python の Web フレームワーク / 型で検証するライブラリ | `gui/server.py` |

## 確認問題

1. 型ヒント(`Literal`)だけでは、実行時に間違いを止められないのは、なぜですか。止めるには何を使いますか。
2. `PadBackend` を `Protocol` にすると、テストがしやすくなるのは、なぜですか。
3. Watchdog が、1 回の失敗で作り直さないのは、なぜですか。
4. ブロックリスト式のサンドボックスが、破られやすいのは、なぜですか。

## 解答

1. 型ヒントは実行時には検査されない注釈だから。pydantic のように、実行時に検査する仕組みを使う。
2. 本物のコントローラーの代わりに、同じメソッドを持つ偽物を渡して、実機なしで動かせるから。
3. 一時的な失敗(揺れ)で計画を壊さないため。しきい値まで、または回復不能と診断されるまで待つ。
4. 禁止し忘れた道が 1 つでもあれば抜けられるから(許可リスト式は、許したものしか通らない)。
