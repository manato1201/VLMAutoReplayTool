"""Phase4/Final Phase: RAGはTODO生成時のみ使用するというアーキテクチャ境界の静的検証。"""
from __future__ import annotations

import ast
import inspect

from vlm_auto_replay.loop import main_loop as main_loop_module
from vlm_auto_replay.loop import watchdog as watchdog_module
from vlm_auto_replay.prompts import functions as functions_module


def _imported_module_names(module) -> set[str]:
    """docstring中の言及ではなく、実際のimport文だけを対象にモジュール名を抽出する。"""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_functions_module_never_imports_rag_client():
    """9関数(generate_next_actionを含む)を定義するモジュールがRAGクライアントを一切importしないこと。"""
    imported = _imported_module_names(functions_module)
    assert not any("rag_client" in name for name in imported)


def test_main_loop_module_never_imports_rag_client():
    imported = _imported_module_names(main_loop_module)
    assert not any("rag_client" in name for name in imported)


def test_watchdog_module_never_imports_rag_client():
    """Watchdog.rebuild_todoは現在TODOの過去ログのみを使用し、RAGクライアントは使用しない。"""
    imported = _imported_module_names(watchdog_module)
    assert not any("rag_client" in name for name in imported)


def test_generate_next_action_call_triggers_zero_rag_calls(scripted_client, monkeypatch):
    """1ステップ実行中(generate_next_action〜execute)のRAG呼び出しが0回であることを実証する。"""
    import vlm_auto_replay.knowledge.rag_client as rag_client_module
    from vlm_auto_replay.prompts.functions import generate_next_action
    from vlm_auto_replay.prompts.schemas import NextActionOutput, TodoItem

    call_count = {"n": 0}

    def _fail_if_called(self, *args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("generate_next_action実行中にRAGクライアントが呼び出されました")

    monkeypatch.setattr(rag_client_module.VLMReplayRagClient, "search", _fail_if_called)

    scripted_client.queue("generate_next_action", NextActionOutput(actionType="api", actionId="advance", params={}))
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    generate_next_action(todo, b"obs", [])

    assert call_count["n"] == 0
