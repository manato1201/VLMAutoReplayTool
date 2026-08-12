"""Phase4: 経験知識(Experience)とTODO生成の前段オーケストレーション。

RAG呼び出しの注入先は decompose_goal_to_todo の"呼び出し元"であるこのモジュールに限定する。
prompts.functions は rag_client を一切importしないため、generate_next_action 実行経路には
RAG呼び出しが物理的に存在しない。

TODO生成に添付するのは Experience.summary のみ(problem/betterWay全体は添付しない、
ユーザー原文の厳密な指定)。
"""

from __future__ import annotations

from pydantic import BaseModel

from ..prompts.functions import decompose_goal_to_todo
from ..prompts.schemas import DecomposeGoalOutput
from .rag_client import VLMReplayRagClient


class Experience(BaseModel):
    title: str
    summary: str  # decompose_goal_to_todoに添付されるのはこのフィールドのみ
    problem: str
    betterWay: str


def decompose_goal_with_rag(
    goal: str, rag_client: VLMReplayRagClient
) -> DecomposeGoalOutput:
    """RAG検索を行ってから decompose_goal_to_todo を呼び出すTODO生成の唯一の入口。

    docs namespaceにはマニュアル・wiki・攻略本、experience namespaceには
    extract_experience が抽出したExperienceを投入する運用を前提とする。
    """
    docs_result = rag_client.search(goal, namespace="docs")
    experience_result = rag_client.search(goal, namespace="experience")

    rag_context = _extract_text_snippets(docs_result) + _extract_experience_summaries(
        experience_result
    )
    return decompose_goal_to_todo(goal=goal, rag_context=rag_context)


def _extract_text_snippets(search_result: dict) -> list[str]:
    return [
        item.get("text", "")
        for item in search_result.get("results", [])
        if item.get("text")
    ]


def _extract_experience_summaries(search_result: dict) -> list[str]:
    summaries: list[str] = []
    for item in search_result.get("results", []):
        payload = item.get("metadata") or item
        summary = payload.get("summary")
        if summary:
            summaries.append(summary)
    return summaries
