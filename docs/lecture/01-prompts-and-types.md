# 第 1 回 型つきのプロンプト層(Pydantic と RAG の境界)

> 所要時間の目安: 2.5 時間 / 前提: 第 0 回・型ヒントの基本

## この回のゴール

- モデルの出力を **Pydantic のスキーマ**で受ける理由(**検証**)を説明できる。
- `Literal`・`model_validator` による、**不正な組み合わせを作らせない**書き方が分かる。
- **RAG は TODO を作る前段だけで使い、行動の生成には持ち込まない**という境界と、その守り方を説明できる。

## 1. モデルの出力は、型で受ける

モデルは自由な文章を返します。そのまま使うと、**形が崩れた答え**(キーが無い・型が違う)で、後ろの処理が壊れます。そこで、**期待する形**を `BaseModel` で宣言し、**受け取った時点で検証**します。

```python
# 引用: src/vlm_auto_replay/prompts/schemas.py::NextActionOutput
class NextActionOutput(BaseModel):
    actionType: Literal["skill", "api"]
    actionId: str
    params: dict
```

- `actionType: Literal["skill", "api"]`: **この 2 つの文字列だけ**。`"jump"` のような未知の種類は、**その場で `ValidationError`**。
- `params: dict`: 行動のパラメータ。

```python
# 実行できる
from pydantic import ValidationError

from vlm_auto_replay.prompts.schemas import NextActionOutput, StallDiagnosis

ok = NextActionOutput(actionType="api", actionId="key_input", params={"key": "W", "hold_ms": 100})
print("正しい形:", ok.actionType, ok.actionId, ok.params)

for label, raw in {
    "未知の種類": {"actionType": "jump", "actionId": "x", "params": {}},
    "キーが足りない": {"actionType": "api", "actionId": "x"},
    "params が辞書でない": {"actionType": "api", "actionId": "x", "params": "W"},
}.items():
    try:
        NextActionOutput(**raw)
    except ValidationError as exc:
        print(f"{label}: 拒否({exc.errors()[0]['type']})")

# 文字列の "true" など、型の合うものは変換される(Pydantic の既定)
print("変換:", StallDiagnosis(rootCause="同じ場所", nextApproach="別の道", shouldRebuildTodo="true").shouldRebuildTodo)
```

```text
# 出力
正しい形: api key_input {'key': 'W', 'hold_ms': 100}
未知の種類: 拒否(literal_error)
キーが足りない: 拒否(missing)
params が辞書でない: 拒否(dict_type)
変換: True
```

> **検証の意味**: モデルの出力は**信用しない**。形が合わなければ、**ループの中で壊れる前に**、入口で止める。

## 2. 不正な組み合わせを、作らせない(`Skill`)

スキルは 2 種類: **procedure**(手順書の文章)と **script**(Python のコード)。**種類ごとに必須の項目が違う**ので、矛盾した組み合わせは、作った時点で弾きます。

```python
# 引用: src/vlm_auto_replay/actions/skill.py::Skill
class Skill(BaseModel):
    skillId: str
    gameTitle: str
    type: Literal["procedure", "script"]
    proceduralText: str | None = None  # type=="procedure"のみ
    scriptCode: str | None = None  # type=="script"のみ
    paramSchema: dict
    createdBy: Literal["manual", "auto"]
    sourceTrace: list[str] | None = None  # 自動生成時の元ソース参照(動画/ログ)

    @model_validator(mode="after")
    def _check_type_fields(self) -> Skill:
        if self.type == "procedure" and self.proceduralText is None:
            raise ValueError("type=='procedure' には proceduralText が必須です。")
        if self.type == "script" and self.scriptCode is None:
            raise ValueError("type=='script' には scriptCode が必須です。")
        if self.type == "procedure" and self.scriptCode is not None:
            raise ValueError("type=='procedure' に scriptCode を設定してはいけません。")
        if self.type == "script" and self.proceduralText is not None:
            raise ValueError("type=='script' に proceduralText を設定してはいけません。")
        if self.createdBy == "auto" and not self.sourceTrace:
            raise ValueError("createdBy=='auto' の場合 sourceTrace は非空である必要があります。")
        return self
```

`@model_validator(mode="after")` は、**全項目がそろったあと**に走る検査です。

- procedure には `proceduralText` が**必須**、`scriptCode` を**付けてはいけない**。script はその逆。
- **自動生成**(`createdBy == "auto"`)なら、**元の出どころ**(`sourceTrace`)が必須(後で「どこから作られたか」を追えるように)。

```python
# 実行できる
from pydantic import ValidationError

from vlm_auto_replay.actions.skill import Skill

base = dict(skillId="s1", gameTitle="DemoGame", paramSchema={})
cases = {
    "正しい procedure": dict(type="procedure", proceduralText="1. 右へ進む", createdBy="manual"),
    "正しい script": dict(type="script", scriptCode="api.key_input('W', 100)", createdBy="manual"),
    "procedure に手順が無い": dict(type="procedure", createdBy="manual"),
    "procedure にコードがある": dict(type="procedure", proceduralText="x", scriptCode="y", createdBy="manual"),
    "自動生成に出どころが無い": dict(type="script", scriptCode="pass", createdBy="auto"),
}
for label, extra in cases.items():
    try:
        Skill(**base, **extra)
        print(f"{label}: OK")
    except ValidationError as exc:
        print(f"{label}: 拒否 →", exc.errors()[0]["msg"].replace("Value error, ", ""))
```

```text
# 出力
正しい procedure: OK
正しい script: OK
procedure に手順が無い: 拒否 → type=='procedure' には proceduralText が必須です。
procedure にコードがある: 拒否 → type=='procedure' に scriptCode を設定してはいけません。
自動生成に出どころが無い: 拒否 → createdBy=='auto' の場合 sourceTrace は非空である必要があります。
```

## 3. RAG の境界: 「TODO を作る前」だけ

**RAG**(検索で拾った知識を、モデルに渡す仕組み)は便利ですが、**行動を生成する最中**に使うと、毎ステップ検索が走り、**遅く・ぶれ**ます。そこで境界を決めています。

| 場面 | RAG | 理由 |
|---|---|---|
| TODO の生成(`decompose_goal_with_rag`) | **使う** | 1 回だけ。計画の材料として、知識を足す |
| 次の行動の生成(`generate_next_action`) | **使わない** | 毎ステップ呼ばれる。速さと、挙動の安定を優先 |
| TODO の再構築(Watchdog) | **使わない** | 入力は、現在の TODO と、その過去の試行ログだけ |

**守り方**: `prompts/functions.py` は、**`rag_client` を import しない**。import しないものは、**呼べません**。さらに、テストで、それを確かめます。

```python
# 実行できる
import ast
from pathlib import Path

source = Path("src/vlm_auto_replay/prompts/functions.py").read_text(encoding="utf-8")
imported = set()
for node in ast.walk(ast.parse(source)):
    if isinstance(node, ast.ImportFrom):
        imported.add((node.module or "").split(".")[-1])
    elif isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[-1] for alias in node.names)

print("rag_client を import している?", "rag_client" in imported)
print("import しているモジュール:", sorted(imported - {"annotations"}))
```

```text
# 出力
rag_client を import している? False
import しているモジュール: ['__future__', 'model_client', 'schemas']
```

**ソースを構文木(`ast`)で調べる**のは、「**このモジュールは、あれに依存していない**」という**設計上の約束**を、テストで守るやり方です(文字列検索より確実。コメントの中の `rag_client` に惑わされない)。

`generate_next_action` が受け取るのは、**非強制のガイダンス**(`guidance_text`)だけ。手順書のスキルを使うときの参考で、**従わなくても止めない**(モデルが状況を見て、逸脱してよい)。

## 確認問題

1. モデルの出力を、`BaseModel` で受けて検証するのは、なぜですか。
2. `Literal["skill", "api"]` と、ただの `str` の違いは。
3. `@model_validator(mode="after")` で、何を検査していますか。
4. RAG を、行動の生成に使わない理由を 2 つ挙げてください。
5. 「`functions.py` が `rag_client` を import していない」ことを、`ast` で確かめるのは、なぜですか。

## 解答

1. 形が崩れた答え(キー不足・型違い)で、**後ろの処理が壊れる前に**、入口で止めるため。モデルの出力は、信用しない。
2. `Literal` は、**列挙した値だけ**を許す。`str` だと、`"jump"` のような未知の値も通り、後ろで `KeyError` などになる。
3. 全項目がそろった後の、**項目どうしの整合**(種類ごとの必須・禁止、自動生成なら出どころ必須)。
4. 毎ステップ呼ばれるので、**検索で遅くなる**。検索の結果で、**挙動がぶれる**(同じ画面で違う行動になる)。
5. 「依存していない」という**設計の約束**を、コードの変更で破られないように守るため。文字列の検索だと、コメントや文字列に惑わされる。

## まとめ

- モデルの出力は、**Pydantic で受けて検証**する。`Literal` と `model_validator` で、不正な組み合わせを作らせない。
- RAG は **TODO 生成の前段だけ**。行動の生成には持ち込まない。**import しない**ことで守り、`ast` のテストで確かめる。
- 次の回は、画面を撮り、行動を選び、実行し、**状態の変化を確かめる**メインループ。
