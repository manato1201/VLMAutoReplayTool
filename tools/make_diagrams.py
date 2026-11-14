"""docs/diagrams/ の構成図(SVG)を作る。直すときは、ここを直して `python tools/make_diagrams.py` を実行する。

  architecture.svg   全体の構成(計画=基盤モデル側・実行=ループとアクション・操作 GUI)

部品は tools/svgkit.py。座標は手で決める。決まり: ①矢印はカードの横(か上)から出入り(カードの下には文字がある)
②縦に渡るときは、カードの間のすき間を通る ③流れの説明は、図に詰めず、本文の Mermaid(シーケンス図)に書く。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from svgkit import Diagram  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "diagrams"


def architecture() -> Diagram:
    d = Diagram(1400, 1090, "VLMAutoReplayTool の構成",
                "基盤モデルは、型つきの 9 つの関数(prompts)を通して、計画だけを受け持つ。メインループが、画面を撮り、次の行動を選び、"
                "ActionDispatcher が、許可された API の入力か、サンドボックスで動くスクリプトのスキルを実行する。Watchdog が停滞を見張り、TODO を作り直す。")
    d.text(40, 44, "VLMAutoReplayTool — 構成", cls="hd")
    d.text(40, 66, "青=主な流れ / 灰の破線=補助。計画(モデル)と実行(専用の部品)を分ける。モデルは Protocol 越しで差し替えられる", cls="note")

    # ---- 左: 計画 ----
    d.group(40, 100, 250, 740, "PLAN — 計画(モデル側)", color="purple")
    model = d.card(150, 190, "cloud", "基盤モデル(VLM)", "FoundationModelClient\n(Protocol・差し替え可)")
    prompts = d.card(150, 340, "list", "prompts/", "9 つの型つきの関数\nPydantic で検証")
    d.card(150, 490, "doc", "knowledge/", "RAG は TODO 生成の前だけ\ndocs・experience")
    d.card(150, 640, "check", "skills/", "自動抽出 +\nホールドアウトの受け入れ")

    # ---- 中央: 実行 ----
    d.group(480, 100, 860, 740, "RUN — 実行(loop/ + actions/)", color="teal")
    loop = d.card(640, 340, "terminal", "MainLoop", "撮る→選ぶ→実行→確認\n理由が空なら実行しない")
    dog = d.card(900, 340, "pulse", "Watchdog", "診断 or 15 ステップで\nTODO を作り直す")
    disp = d.card(1160, 340, "gear", "ActionDispatcher", "api / skill の振り分け\nAPI は許可リストだけ")
    sandbox = d.card(900, 560, "lock", "ScriptSandbox", "制限した exec\n(ブロックリスト方式)")
    api = d.card(1160, 560, "wave", "ApiPrimitives", "パッド・キー・マウス\nOCR・移動")
    game = d.card(1160, 720, "video", "ゲーム", "実機: ViGEm・SendInput\nデモ: DemoGame")
    d.card(640, 560, "db", "StepLog", "理由・行動・結果の要約\n画像は参照で保存")
    d.text(500, 825, "GUI(gui/): 操作・HUD・認証・監査。ただし GUI の実行は、いまはデモのゲーム・デモのモデルだけ", cls="note")

    # ---- 下: GUI ----
    d.group(480, 890, 860, 160, "GUI — FastAPI + 静的画面", color="blue")
    d.card(640, 955, "browser", "操作パネル", "operator: 実行の開始・停止")
    d.card(900, 955, "chart", "監視 HUD", "WebSocket・本番は既定で無効")
    d.card(1160, 955, "shield", "認証・監査", "viewer / operator / admin")

    # ---- 矢印 ----
    d.arrow([model.left, (70, 190), (70, 340), prompts.left], "complete()", at=1, dx=22, dy=32)
    d.arrow([prompts.right, loop.left], "次の行動・理由・要約", both=True, at=0)
    d.arrow([loop.right, dog.left], "介入する?", both=True, at=0)
    d.arrow([loop.top, (640, 210), (1160, 210), disp.top], "行動を実行", at=1, dy=-10)
    d.arrow([disp.right, (1280, 340), (1280, 430), (1160, 430), api.top], "api", kind="primary", at=0, dx=0, dy=-10)
    d.arrow([disp.left, (1040, 340), (1040, 440), (900, 440), sandbox.top], "script スキル", kind="primary", at=1, dx=-20, dy=-10)
    d.arrow([sandbox.right, api.left], "api 経由だけ", at=0)
    d.arrow([api.right, (1280, 560), (1280, 720), game.right], "入力", at=1, dx=-26, dy=0)
    d.arrow([(640, 445), (640, 528)], "ログ", kind="secondary", at=0, dx=22, dy=0)
    return d


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "architecture.svg").write_text(architecture().svg(), encoding="utf-8")
    print("wrote", OUT / "architecture.svg")


if __name__ == "__main__":
    main()
