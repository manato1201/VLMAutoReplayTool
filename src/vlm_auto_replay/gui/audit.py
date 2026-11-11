"""gui/audit.py — 監査ログ(誰が・いつ・何をしたか)。VisualRegressionQATool の app/audit.py と同じ作り(2026-11-10 に移植)。

複数人で使うとき、実行を開始・停止した、スキルを足した・消した、入力バックエンドを変えた、といった**変更**と、**拒否された操作**(401 / 403 / WebSocket の拒否)を残す。
JSON Lines(1 行 1 件)で追記する。**本文・API キー・クッキー・クエリ文字列は記録しない**(パスと結果だけ)。

- 記録する: 変更系のメソッド(POST / PUT / PATCH / DELETE)の /api/*、401・403(認証・権限の失敗)、ログイン・ログアウト。
- 記録しない: 読み取り(GET)の成功、/api/health。
- 保存先: ``VLM_AUDIT_LOG``(既定 ``~/.vlm_auto_replay/audit.jsonl``)。5 MB を超えたら ``.1`` に退避する(1 世代)。
- 読み出し: admin だけ ``GET /api/auth/audit?limit=100``。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

AUDIT_ENV = "VLM_AUDIT_LOG"
DEFAULT_AUDIT_LOG = Path.home() / ".vlm_auto_replay" / "audit.jsonl"
MAX_BYTES = 5 * 1024 * 1024
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEBUG_PAGES = frozenset({"/hud.html", "/hud.js"})  # 開発者・デバッグ向け(server.py の auth.debug)
NOT_LOGGED_PATHS = frozenset({"/api/health"})
SELF_LOGGED_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})

_lock = threading.Lock()


def audit_path() -> Path:
    raw = os.environ.get(AUDIT_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_AUDIT_LOG


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def record(event: str, *, actor: str | None = None, role: str | None = None, source: str | None = None,
           method: str | None = None, path: str | None = None, status: int | None = None,
           ip: str | None = None, detail: str | None = None) -> None:
    """1 行を追記する。書けなくても、本来の処理は止めない(ログだけ残す)。"""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "actor": actor or "anonymous",
        "role": role,
        "source": source,
        "method": method,
        "path": path,
        "status": status,
        "ip": ip,
        "detail": detail,
    }
    line = json.dumps({k: v for k, v in entry.items() if v is not None}, ensure_ascii=False)
    try:
        path = audit_path()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                path.replace(path.with_suffix(path.suffix + ".1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError as exc:
        logger.error("監査ログを書けません: %s", exc)


def read_recent(limit: int = 100) -> list[dict]:
    """新しい順に最大 limit 件。壊れた行は読み飛ばす。"""
    path = audit_path()
    if not path.exists():
        return []
    with _lock:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def install_audit(app: FastAPI) -> None:
    """変更系と、認証・権限の失敗を記録するミドルウェア。"""

    @app.middleware("http")
    async def audit_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # /api/* のほか、開発者・デバッグ向けのページ(監視 HUD)への拒否も記録する(許可された閲覧は、読み取りなので記録しない)
        if (path.startswith("/api/") or path in DEBUG_PAGES) and path not in NOT_LOGGED_PATHS and not path.startswith("/api/auth/audit"):
            if path in SELF_LOGGED_PATHS and response.status_code != 403:
                return response  # ログイン・ログアウトは routers/auth.py が名前つきで記録する(ガードの 403 だけ、ここで)
            denied = response.status_code in (401, 403)
            if request.method in UNSAFE_METHODS or denied:
                principal = getattr(request.state, "principal", None)
                record(
                    "denied" if denied else "http",
                    actor=getattr(principal, "name", None),
                    role=getattr(principal, "role", None),
                    source=getattr(principal, "source", None),
                    method=request.method,
                    path=path,
                    status=response.status_code,
                    ip=_client_ip(request),
                )
        return response
