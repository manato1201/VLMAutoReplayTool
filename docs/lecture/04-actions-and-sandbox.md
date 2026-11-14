# 第 4 回 実行の層: API の入力・スキル・サンドボックス

> 所要時間の目安: 3.5 時間 / 前提: 第 0〜3 回

## この回のゴール

- 行動が **「API」と「スキル」の 2 種類**で、**どう振り分けられる**かを説明できる。
- **ホワイトリスト**で、モデルが**呼べるものを絞る**理由を説明できる。
- スクリプトを**制限つきの環境で実行**するしくみ(`exec` と制限した `__builtins__`)を説明できる。
- **ブロックリストの限界**を、**実際に破れる例**で確かめ、**許可リスト**への考え方を説明できる。

## 1. 行動の 2 種類

| `actionType` | 何か | 実行 |
|---|---|---|
| `api` | **1 回の入力**(ボタンを押す・キーを押す・マウスを動かす・OCR・移動) | `ApiPrimitives` のメソッド |
| `skill` | **保存済みのスキル**(まとまった操作) | `script` なら Python を実行。`procedure` は別の道(下) |

入力は、本番では **ViGEm**(仮想ゲームパッド)や **SendInput**(キーボード・マウス)ですが、これらは**実機のドライバが要る**ので、**`Protocol` で抽象化**して、テスト用の **Null 実装**(呼ばれた内容を覚えるだけ)を用意しています。

## 2. ディスパッチャ: 呼べるものを絞る

```python
# 引用: src/vlm_auto_replay/actions/dispatcher.py::ActionDispatcher._dispatch_api
def _dispatch_api(self, action_id: str, params: dict) -> None:
    if action_id not in _ALLOWED_API_PRIMITIVES:
        raise KeyError(f"未知のAPI primitiveです: {action_id}")
    primitive = getattr(self._api, action_id)
    primitive(**params)
```

```python
# 抜粋ファイル: src/vlm_auto_replay/actions/dispatcher.py
_ALLOWED_API_PRIMITIVES = frozenset({"pad_input", "key_input", "mouse_move", "ocr", "movement"})
```

`getattr(self._api, action_id)` で、**文字列から関数を取り出して呼ぶ**のは便利ですが、**危険**です。モデルが返す `actionId` が、`"_km"`(内部の部品)や、将来足す**内部用のメソッド**でも、そのまま呼べてしまいます。そこで、**明示的な許可リスト**(`frozenset`)にあるものだけを通します。

> **原則**: 外から来た**文字列**で、内部の関数を選ぶときは、**許可するものを列挙する**(許可しないものを列挙するのでなく)。

procedure スキルは、1 ステップでは実行できません(`MainLoop.run` そのものを、再帰的に起動する形になるため)。`script` だけが、ここで実行されます。

```python
# 実行できる
from vlm_auto_replay.actions.api_primitives import (
    ApiPrimitives, NullKeyboardMouseBackend, NullPadBackend, ScriptedOcrBackend,
)
from vlm_auto_replay.actions.dispatcher import ActionDispatcher
from vlm_auto_replay.actions.sandbox import ScriptSandbox
from vlm_auto_replay.actions.skill import Skill
from vlm_auto_replay.actions.skill_runner import SkillRunner
from vlm_auto_replay.prompts.schemas import NextActionOutput

pad, km = NullPadBackend(), NullKeyboardMouseBackend()
api = ApiPrimitives(pad, km, ScriptedOcrBackend(default_text="HP 80"))
skills = {
    "dash": Skill(skillId="dash", gameTitle="Demo", type="script", scriptCode="api.movement('right', params['ms'])",
                  paramSchema={}, createdBy="manual"),
    "guide": Skill(skillId="guide", gameTitle="Demo", type="procedure", proceduralText="1. 右へ", paramSchema={}, createdBy="manual"),
}
dispatcher = ActionDispatcher(api, skills, SkillRunner(None, ScriptSandbox(api)))


def act(kind, action_id, params):
    try:
        dispatcher.execute(NextActionOutput(actionType=kind, actionId=action_id, params=params))
        return "OK"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:34]}"


print("パッド     :", act("api", "pad_input", {"button": "A", "hold_ms": 80}), "→", pad.calls)
print("移動       :", act("api", "movement", {"direction": "up", "duration_ms": 200}), "→", km.key_calls)
print("内部の部品 :", act("api", "_km", {}))
print("未知の入力 :", act("api", "format_disk", {}))
print("script     :", act("skill", "dash", {"ms": 300}), "→", km.key_calls[-1])
print("procedure  :", act("skill", "guide", {}))
print("存在しない :", act("skill", "nothing", {}))
print("未知の方向 :", act("api", "movement", {"direction": "diagonal", "duration_ms": 1}))
```

```text
# 出力
パッド     : OK → [('A', 80)]
移動       : OK → [('w', 200)]
内部の部品 : KeyError: '未知のAPI primitiveです: _km'
未知の入力 : KeyError: '未知のAPI primitiveです: format_disk'
script     : OK → ('d', 300)
procedure  : ValueError: procedureスキル(guide)は1ステップ実行できません。 
存在しない : KeyError: '未知のskillIdです: nothing'
未知の方向 : ValueError: 未知の移動方向です: diagonal
```

## 3. サンドボックス: スクリプトの実行を制限する

**script スキルは、Python のコード**です。**モデルが自動生成**したものも含まれます。そのまま `exec` すると、**ファイルを読み書き・ネットワークに接続・プロセスを起動**できてしまいます。誤って(あるいは悪意で)危険なコードが入ると、困ります。

そこで、**制限した環境**で実行します。

```python
# 引用: src/vlm_auto_replay/actions/sandbox.py::ScriptSandbox.execute
def execute(self, script_code: str, params: dict) -> None:
    restricted_builtins = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
    restricted_builtins["__import__"] = self._guarded_import
    global_ns: dict = {"__builtins__": restricted_builtins, "api": self._api, "params": params}
    compiled = compile(script_code, "<skill_script>", "exec")
    exec(compiled, global_ns, {})  # noqa: S102 - scriptスキル実行そのものがこのサンドボックスの目的
```

1. **`__builtins__` を、安全な組み込み関数だけ**にする(`abs`・`len`・`range`・`print` など)。`open`・`exec`・`eval`・`type` は**無い**。
2. **`__import__` を差し替え**て、**危険なモジュール**(`os`・`sys`・`subprocess`・`socket`・`requests`・`pathlib` など)の import を禁止する。
3. スクリプトの**グローバル**は、**`api`(入力の部品)と `params` だけ**。ゲームを動かすのは `api` 経由のみ。

```python
# 実行できる
from vlm_auto_replay.actions.api_primitives import (
    ApiPrimitives, NullKeyboardMouseBackend, NullPadBackend, ScriptedOcrBackend,
)
from vlm_auto_replay.actions.sandbox import SandboxViolationError, ScriptSandbox

km = NullKeyboardMouseBackend()
sandbox = ScriptSandbox(ApiPrimitives(NullPadBackend(), km, ScriptedOcrBackend()))


def run(code, params=None):
    try:
        sandbox.execute(code, params or {})
        return "OK"
    except SandboxViolationError as exc:
        return f"禁止: {str(exc)[:32]}"
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:36]}"


print("普通の操作  :", run("for i in range(3):\n    api.key_input('W', params['ms'])", {"ms": 50}), "→", len(km.key_calls), "回")
print("import os   :", run("import os"))
print("import socket:", run("import socket"))
print("open()      :", run("open('x.txt')"))
print("eval()      :", run("eval('1+1')"))
print("構文の誤り  :", run("api.key_input("))
```

```text
# 出力
普通の操作  : OK → 3 回
import os   : 禁止: サンドボックス内では 'os' のインポートは禁止されています。
import socket: 禁止: サンドボックス内では 'socket' のインポートは禁止されて
open()      : NameError: name 'open' is not defined
eval()      : NameError: name 'eval' is not defined
構文の誤り  : SyntaxError: '(' was never closed (<skill_script>
```

## 4. ブロックリストの限界(実際に破れる)

このサンドボックスは、**禁止するものを列挙する方式**(**ブロックリスト**)です。ソースのコメントも、「完全な安全境界ではない」と書いています。実際に、**簡単に破れる**ことを確かめましょう。

```python
# 実行できる
from vlm_auto_replay.actions.api_primitives import (
    ApiPrimitives, NullKeyboardMouseBackend, NullPadBackend, ScriptedOcrBackend,
)
from vlm_auto_replay.actions.sandbox import ScriptSandbox

sandbox = ScriptSandbox(ApiPrimitives(NullPadBackend(), NullKeyboardMouseBackend(), ScriptedOcrBackend()))

# 「os」の import は、直接なら止まる。しかし builtins モジュール自体は、禁止リストに無い。
sandbox.execute("import builtins\nprint('builtins は import できた:', builtins.__import__('os').__name__)", {})

# api の「クラス」から、元のモジュールの名前空間に届く
sandbox.execute("print('api の関数が属する名前空間に届く:', api.__class__.__init__.__globals__['__name__'])", {})
```

```text
# 出力
builtins は import できた: os
api の関数が属する名前空間に届く: vlm_auto_replay.actions.api_primitives
```

**何が起きたか**: ① `builtins` は禁止リストに無いので import でき、そこに**本物の `__import__`** があるため、**`os` を取り出せる**。② `api.__class__.__init__.__globals__` を辿ると、**`api` を定義したモジュールの名前空間**に届く(そこには `ctypes` や `time` が import されている)。

**教訓**: ブロックリストは、**列挙し忘れたもの**が、すべて通ります。**抜け道は、いくらでもある**。

| 方式 | 考え方 | 強さ |
|---|---|---|
| ブロックリスト(今) | 「**これは禁止**」を列挙。それ以外は通る | **弱い**(列挙漏れ・抜け道) |
| **許可リスト** | 「**これだけ許可**」を列挙。それ以外は拒否 | 強い(import は `math` など数個だけ許可) |
| **OS レベルの分離** | 別プロセス・コンテナ・専用ユーザー・ネットワークなし | **最も強い**(コードの中身に依らない) |

**直す案**: ①`_guarded_import` を、**許可するモジュールの許可リスト**に変える(`builtins` は不可)。②`api` を、**クラスの内部を辿れない薄い代理**にする(必要な関数だけを持つ)。③**自動生成のスクリプトは、人が見るまで実行しない**(登録のゲート・第 5 回)。④信頼できないコードには、**OS の分離**(別プロセス・コンテナ)。

> **ここでの現実的な位置づけ**: このサンドボックスの目的は、**自動生成された UI 操作スクリプトが、うっかりファイル・ネットワークに触れない**ことで、**悪意あるコードの封じ込めではない**。自分のスキルだけを動かす間は、実用上の多層防御として足りる。**他人のスキル・ネットで拾ったコード**を動かすなら、OS の分離が必須。

## 確認問題

1. `getattr(api, action_id)` を、そのまま使わず、許可リストを通すのは、なぜですか。
2. サンドボックスで、`open` や `eval` が使えないのは、なぜですか(どのしくみで)。
3. `import os` は止まるのに、`import builtins` から `os` が取り出せたのは、なぜですか。
4. ブロックリストと許可リストの違いを、説明してください。
5. 自動生成されたスクリプトを、すぐに登録・実行しないほうがよいのは、なぜですか。

## 解答

1. `actionId` は**モデルが返す文字列**。許可リストが無いと、内部の部品や、将来足した内部用のメソッドまで、呼べてしまう。
2. スクリプトに渡す `__builtins__` を、**安全な名前だけ**に絞っているので、`open`・`eval` は**定義されていない**(`NameError`)。
3. 禁止リストに `builtins` が**無い**から。`builtins` には本物の `__import__` があり、それを使うと、制限を**迂回**できる。
4. ブロックリストは「禁止を列挙し、他は通す」ので、**列挙漏れが穴**になる。許可リストは「許可を列挙し、他は拒否」なので、**列挙漏れは、安全側**に倒れる。
5. モデルが作ったコードは、**間違い・危険な操作**を含みうる。人が確認するか、ホールドアウトのテスト(第 5 回)で受け入れてから登録する。

## まとめ

- 行動は `api`(1 回の入力)と `skill`(保存済み)。ディスパッチャは、**許可リスト**にあるものだけを呼ぶ。
- スクリプトは、**制限した `__builtins__`・`__import__` の差し替え・限られたグローバル**で実行する。
- ただし**ブロックリストは破れる**(`builtins`・`__globals__`)。**許可リスト**と **OS の分離**が、本物の境界。
- 次の回(最終回)は、スキルの自動抽出・受け入れのゲーム・テストと総合演習。
