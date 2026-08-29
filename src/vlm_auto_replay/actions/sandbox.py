"""Phase3: scriptスキルのサンドボックス実行。

注意: Pythonのexecベースのサンドボックスは完全な安全境界ではない(意図的に難読化された
コードでの脱出は理論上可能)。ここでは「自動生成されたUI操作スクリプトが誤って
ファイル/ネットワークへアクセスしない」ことを目的とした、実用上十分な多層防御として実装する。
真に信頼できない外部コードの実行にはOS levelの分離(コンテナ/専用プロセス)を別途検討すること。
"""
from __future__ import annotations

import builtins

from .api_primitives import ApiPrimitives

_BLOCKED_TOP_LEVEL_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "requests", "urllib",
    "pathlib", "ctypes", "importlib", "io", "http",
}

_SAFE_BUILTIN_NAMES = {
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len",
    "list", "max", "min", "print", "range", "round", "sorted", "str", "sum",
    "tuple", "zip", "True", "False", "None", "isinstance",
}


class SandboxViolationError(Exception):
    pass


class ScriptSandbox:
    """type=="script" のSkillを、限定されたグローバル環境で実行する。"""

    def __init__(self, api: ApiPrimitives):
        self._api = api

    def execute(self, script_code: str, params: dict) -> None:
        restricted_builtins = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
        restricted_builtins["__import__"] = self._guarded_import
        global_ns: dict = {"__builtins__": restricted_builtins, "api": self._api, "params": params}
        compiled = compile(script_code, "<skill_script>", "exec")
        exec(compiled, global_ns, {})  # noqa: S102 - scriptスキル実行そのものがこのサンドボックスの目的

    @staticmethod
    def _guarded_import(name: str, *args, **kwargs):
        top_level = name.split(".")[0]
        if top_level in _BLOCKED_TOP_LEVEL_MODULES:
            raise SandboxViolationError(f"サンドボックス内では '{name}' のインポートは禁止されています。")
        return builtins.__import__(name, *args, **kwargs)
