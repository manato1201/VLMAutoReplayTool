"""画像の縮小(2026-11-21)。

ローカルの VLM は、画像が大きいほど入力トークンが増えて遅い(4K の画面全体で約 2000 トークン・約 34 秒を実測)。
最長辺を既定 1280px に縮める。Pillow が無い・読めない・小さい画像は、そのまま送る(縮小は最適化で、必須ではない)。
Pillow は任意の依存(`uv sync --extra vision`)。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import sys

import pytest

from vlm_auto_replay.prompts import openai_compat_client as oc
from vlm_auto_replay.prompts.openai_compat_client import ModelClientError, OpenAICompatClient, client_from_env

from test_openai_compat_client import FakeServer, make  # noqa: F401  (server フィクスチャの再利用)
from test_openai_compat_client import server  # noqa: F401

Image = pytest.importorskip("PIL.Image")


def png(w: int, h: int, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), (30, 90, 160) if mode == "RGB" else (30, 90, 160, 255)).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def test_large_image_is_scaled_down_keeping_aspect_ratio():
    out = oc.downscale_image(png(3840, 2160), 1280)
    assert size_of(out) == (1280, 720)
    assert len(out) < len(png(3840, 2160))


def test_portrait_image_uses_the_longest_edge():
    assert size_of(oc.downscale_image(png(1000, 3000), 1280)) == (427, 1280)


def test_small_image_is_returned_untouched():
    data = png(800, 600)
    assert oc.downscale_image(data, 1280) is data
    exact = png(1280, 720)
    assert oc.downscale_image(exact, 1280) is exact, "ちょうど上限なら縮めない"


def test_zero_disables_downscaling():
    data = png(3840, 2160)
    assert oc.downscale_image(data, 0) is data


def test_jpeg_stays_jpeg_and_png_stays_png():
    out = oc.downscale_image(jpeg(2560, 1440), 1280)
    assert out.startswith(b"\xff\xd8\xff") and size_of(out) == (1280, 720)
    out = oc.downscale_image(png(2560, 1440, "RGBA"), 1280)
    assert out.startswith(b"\x89PNG") and size_of(out) == (1280, 720)


def test_undecodable_bytes_pass_through():
    fake = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
    assert oc.downscale_image(fake, 1280) is fake
    assert oc.downscale_image(b"", 1280) == b""


def test_without_pillow_passes_through_and_warns_once(monkeypatch, caplog):
    data = png(3840, 2160)  # Pillow を隠す前に作る
    monkeypatch.setitem(sys.modules, "PIL", None)  # import PIL が ImportError になる
    monkeypatch.setattr(oc, "_warned_no_pillow", False)
    with caplog.at_level(logging.WARNING, logger=oc.__name__):
        assert oc.downscale_image(data, 1280) is data
        assert oc.downscale_image(data, 1280) is data
    warnings = [r for r in caplog.records if "Pillow" in r.getMessage()]
    assert len(warnings) == 1, "警告は 1 回だけ"


def sent_image(server) -> bytes:
    url = server.requests[-1]["json"]["messages"][-1]["content"][1]["image_url"]["url"]
    return base64.b64decode(url.split(",", 1)[1])


def reply_ok(server):
    server.responses.append((200, {"choices": [{"message": {"content": "ok"}}]}, 0))


def test_client_downscales_by_default(server):
    reply_ok(server)
    make(server).chat("q", images=[png(3840, 2160)])
    assert size_of(sent_image(server)) == (1280, 720)


def test_client_respects_argument_and_env(server):
    reply_ok(server)
    make(server, max_image_edge=640).chat("q", images=[png(3840, 2160)])
    assert size_of(sent_image(server)) == (640, 360)
    reply_ok(server)
    make(server, environ={"VLM_LLM_MAX_IMAGE_EDGE": "960"}).chat("q", images=[png(3840, 2160)])
    assert size_of(sent_image(server)) == (960, 540)
    reply_ok(server)
    make(server, max_image_edge=0).chat("q", images=[png(3840, 2160)])
    assert size_of(sent_image(server)) == (3840, 2160)


def test_invalid_setting_is_rejected(server):
    with pytest.raises(ValueError):
        make(server, max_image_edge=-1)
    with pytest.raises(ValueError):
        make(server, environ={"VLM_LLM_MAX_IMAGE_EDGE": "big"})


def test_client_from_env_passes_the_setting_through():
    env = {"VLM_MODEL_BACKEND": "openai-compat", "VLM_LLM_BASE_URL": "http://127.0.0.1:8800/v1",
           "VLM_LLM_MODEL": "llh-vision", "VLM_LLM_MAX_IMAGE_EDGE": "800"}
    client = client_from_env(env)
    assert isinstance(client, OpenAICompatClient) and client._max_image_edge == 800
    with pytest.raises(ModelClientError):
        client_from_env({**env, "VLM_LLM_MAX_IMAGE_EDGE": "x"})
    assert json.dumps(env)  # env は変更されない
