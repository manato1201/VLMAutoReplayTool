"""OpenAI 互換クライアントのテスト。本物のモデルは使わず、http.server の偽サーバーで検証する。"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vlm_auto_replay import ask as ask_module
from vlm_auto_replay.prompts.model_client import configure_model_client, reset_model_client
from vlm_auto_replay.prompts.openai_compat_client import (
    InvalidEndpointError,
    ModelClientError,
    OpenAICompatClient,
    client_from_env,
    validate_base_url,
)
from vlm_auto_replay.prompts.schemas import ExtractedSubroutine, NextActionOutput, TodoItem

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
SECRET = "sk-test-SECRET-1234567890"


class FakeServer:
    """受けた要求を記録し、あらかじめ並べた応答(status, body, delay)を順に返す。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[tuple[int, object, float]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                outer.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "json": json.loads(raw or b"{}")}
                )
                status, body, delay = outer.responses.pop(0) if outer.responses else (200, outer.chat("{}"), 0)
                if delay:
                    threading.Event().wait(delay)
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # クライアントがタイムアウトで切断した場合

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @staticmethod
    def chat(content: str) -> dict:
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    def queue(self, status: int, body: object, delay: float = 0) -> None:
        self.responses.append((status, body, delay))

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def make(server, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return OpenAICompatClient(base_url=server.url, model="llava", **kw)


ACTION = {"actionType": "api", "actionId": "advance", "params": {"step": 1}}


def test_request_shape_and_image_data_uri(server):
    server.queue(200, FakeServer.chat(json.dumps(ACTION)))
    todo = TodoItem(todoId="t1", description="d", doneCriteria="c")
    out = make(server).complete(
        task="generate_next_action",
        payload={"todo": todo.model_dump(), "history": [], "guidance_text": None},
        images=[PNG],
        response_model=NextActionOutput,
    )
    assert out == NextActionOutput(**ACTION)
    req = server.requests[0]
    assert req["path"] == "/v1/chat/completions"
    body = req["json"]
    assert body["model"] == "llava"
    assert body["response_format"] == {"type": "json_object"}
    system, user = body["messages"]
    assert system["role"] == "system" and "actionType" in system["content"]
    assert user["role"] == "user"
    text_part, image_part = user["content"]
    assert text_part["type"] == "text" and "generate_next_action" in text_part["text"]
    assert image_part["type"] == "image_url"
    url = image_part["image_url"]["url"]
    assert url == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert "Authorization" not in req["headers"]


def test_authorization_header_from_env_name(server):
    server.queue(200, FakeServer.chat(json.dumps(ACTION)))
    client = make(server, api_key_env="MY_KEY_VAR", environ={"MY_KEY_VAR": SECRET})
    client.complete(task="generate_next_action", payload={}, images=None, response_model=NextActionOutput)
    assert server.requests[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in repr(client)


def test_missing_key_env_names_variable_not_value(server):
    client = make(server, api_key_env="MY_KEY_VAR", environ={})
    with pytest.raises(ModelClientError, match="MY_KEY_VAR"):
        client.chat("hi")
    assert server.requests == []


def test_error_never_contains_the_key_even_if_server_echoes_it(server):
    server.queue(401, {"error": f"bad key {SECRET}"})
    client = make(server, api_key_env="K", environ={"K": SECRET})
    with pytest.raises(ModelClientError) as ei:
        client.chat("hi")
    assert SECRET not in str(ei.value) and "401" in str(ei.value) and "***" in str(ei.value)


def test_retry_on_503_then_success_and_exhausted(server):
    server.queue(503, {"error": "busy"})
    server.queue(200, FakeServer.chat("ok"))
    sleeps: list[float] = []
    assert make(server, sleep=sleeps.append).chat("hi") == "ok"
    assert len(server.requests) == 2 and sleeps == [0.5]

    server.queue(500, {})
    server.queue(500, {})
    server.queue(500, {})
    with pytest.raises(ModelClientError, match="HTTP 500"):
        make(server, max_retries=2).chat("hi")


def test_no_retry_on_400(server):
    server.queue(400, {"error": "nope"})
    with pytest.raises(ModelClientError, match="HTTP 400"):
        make(server).chat("hi")
    assert len(server.requests) == 1


def test_timeout(server):
    server.queue(200, FakeServer.chat("late"), delay=1.0)
    server.queue(200, FakeServer.chat("late"), delay=1.0)
    with pytest.raises(ModelClientError, match="タイムアウト"):
        make(server, timeout_sec=0.2, max_retries=1).chat("hi")
    assert len(server.requests) == 2


def test_connection_refused_message():
    c = OpenAICompatClient(base_url="http://127.0.0.1:9/v1", model="m", max_retries=0, sleep=lambda _s: None)
    with pytest.raises(ModelClientError, match="接続できません"):
        c.chat("hi")


def test_redirect_is_not_followed(server):
    server.queue(302, {})
    with pytest.raises(ModelClientError, match="リダイレクト"):
        make(server).chat("hi")
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",  # 外部への平文
        "http://192.168.0.5:11434/v1",  # LAN の平文
        "ftp://127.0.0.1/v1",
        "file:///etc/passwd",
        "http://user:pw@127.0.0.1:11434/v1",  # 認証情報の埋め込み
        "https://user:pw@example.com/v1",
        "http:///v1",
        "http://127.0.0.1:99999/v1",
        "",
        "127.0.0.1:11434/v1",
    ],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(InvalidEndpointError):
        validate_base_url(url)
    with pytest.raises(InvalidEndpointError):
        OpenAICompatClient(base_url=url, model="m")


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434/v1", "http://localhost:8080/v1/", "http://[::1]:1234/v1", "https://api.example.com/v1"],
)
def test_valid_urls_are_accepted(url):
    assert validate_base_url(url) == url.rstrip("/")


def test_json_in_code_fence_and_list_wrapper(server):
    items = [{"subroutineId": "s", "occurrenceCount": 2, "candidateActions": ["a"]}]
    server.queue(200, FakeServer.chat("```json\n" + json.dumps({"items": items}) + "\n```"))
    out = make(server).complete(
        task="extract_reusable_subroutine", payload={}, images=None, response_model=list[ExtractedSubroutine]
    )
    assert out == [ExtractedSubroutine(**items[0])]


def test_schema_mismatch_and_non_json(server):
    server.queue(200, FakeServer.chat(json.dumps({"actionType": "nope"})))
    with pytest.raises(ModelClientError, match="スキーマ"):
        make(server).complete(task="generate_next_action", payload={}, images=None, response_model=NextActionOutput)
    server.queue(200, FakeServer.chat("no json here"))
    with pytest.raises(ModelClientError, match="JSON"):
        make(server).complete(task="generate_next_action", payload={}, images=None, response_model=NextActionOutput)


def test_response_format_none_omits_field(server):
    server.queue(200, FakeServer.chat(json.dumps(ACTION)))
    make(server, response_format="none").complete(
        task="generate_next_action", payload={}, images=None, response_model=NextActionOutput
    )
    assert "response_format" not in server.requests[0]["json"]


def test_plugs_into_existing_functions_via_configure(server):
    from vlm_auto_replay.prompts.functions import generate_next_action

    server.queue(200, FakeServer.chat(json.dumps(ACTION)))
    configure_model_client(make(server))
    try:
        out = generate_next_action(TodoItem(todoId="t", description="d", doneCriteria="c"), PNG, [])
    finally:
        reset_model_client()
    assert out.actionId == "advance"


# --- 設定(環境変数) ---
def test_client_from_env_demo_by_default_and_openai_compat():
    assert client_from_env({}) is None
    assert client_from_env({"VLM_MODEL_BACKEND": "demo"}) is None
    c = client_from_env(
        {"VLM_MODEL_BACKEND": "openai-compat", "VLM_LLM_BASE_URL": "http://127.0.0.1:11434/v1", "VLM_LLM_MODEL": "m"}
    )
    assert isinstance(c, OpenAICompatClient)


@pytest.mark.parametrize(
    "env",
    [
        {"VLM_MODEL_BACKEND": "wat"},
        {"VLM_MODEL_BACKEND": "openai-compat"},
        {"VLM_MODEL_BACKEND": "openai-compat", "VLM_LLM_BASE_URL": "http://evil.example/v1", "VLM_LLM_MODEL": "m"},
        {
            "VLM_MODEL_BACKEND": "openai-compat",
            "VLM_LLM_BASE_URL": "http://127.0.0.1/v1",
            "VLM_LLM_MODEL": "m",
            "VLM_LLM_TIMEOUT_SEC": "abc",
        },
    ],
)
def test_client_from_env_rejects_bad_config(env):
    with pytest.raises(ModelClientError):
        client_from_env(env)


def test_gui_server_uses_env_selected_client(monkeypatch):
    from vlm_auto_replay.gui import server as gui_server

    reset_model_client()
    monkeypatch.setenv("VLM_MODEL_BACKEND", "openai-compat")
    monkeypatch.setenv("VLM_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("VLM_LLM_MODEL", "m")
    try:
        gui_server._ensure_model_client_configured()
        from vlm_auto_replay.prompts.model_client import get_model_client

        assert isinstance(get_model_client(), OpenAICompatClient)
    finally:
        reset_model_client()


# --- ask(別リポジトリ向けの入口) ---
def test_ask_function_and_cli(server, tmp_path, capsys, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(PNG)
    server.queue(200, FakeServer.chat("hello"))
    assert ask_module.ask("what?", image_paths=[img], base_url=server.url, model="m", environ={}) == "hello"
    assert server.requests[0]["json"]["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;")

    monkeypatch.setenv("MY_K", SECRET)
    server.queue(200, FakeServer.chat("cli-answer"))
    rc = ask_module.main(
        ["--base-url", server.url, "--model", "m", "--image", str(img), "--api-key-env", "MY_K", "describe"]
    )
    assert rc == 0 and capsys.readouterr().out.strip() == "cli-answer"
    assert server.requests[1]["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_ask_cli_error_exit_code_hides_key(capsys, monkeypatch):
    monkeypatch.setenv("MY_K", SECRET)
    rc = ask_module.main(["--base-url", "http://example.com/v1", "--model", "m", "--api-key-env", "MY_K", "q"])
    err = capsys.readouterr().err
    assert rc == 1 and "http" in err and SECRET not in err
