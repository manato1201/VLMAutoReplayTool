"""Phase3: パッド・マウス・キーボードの直接操作(APICall primitive)。

Windows HID実装: 仮想コントローラは ViGEm(仮想XInput/DS4、`vgamepad`パッケージ経由)、
キーボード・マウスは SendInput 系フック(ctypes経由)を用いる。

実機ドライバ(ViGEmBus)が無い開発機やCIでもimport/wiringのテストができるよう、
各バックエンドはProtocolとして抽象化し、Null実装(NullPadBackend等)をテスト向けに用意する。
実バックエンドはコンストラクト時にのみ依存解決を試み、import時には失敗しない。
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import ClassVar, Protocol


class PadBackend(Protocol):
    def press(self, button: str, hold_ms: int) -> None: ...


class KeyboardMouseBackend(Protocol):
    def key(self, key: str, hold_ms: int) -> None: ...
    def mouse_move(self, dx: int, dy: int) -> None: ...


class OcrBackend(Protocol):
    def read_text(self, region: tuple[int, int, int, int]) -> str: ...


# ---- 実バックエンド ----------------------------------------------------


class ViGEmPadBackend:
    """ViGEm経由の仮想XInputパッド。`pip install vgamepad` + ViGEmBusドライバが必要。"""

    _BUTTON_ATTR_MAP: ClassVar[dict[str, str]] = {
        "A": "XUSB_GAMEPAD_A",
        "B": "XUSB_GAMEPAD_B",
        "X": "XUSB_GAMEPAD_X",
        "Y": "XUSB_GAMEPAD_Y",
        "LB": "XUSB_GAMEPAD_LEFT_SHOULDER",
        "RB": "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "START": "XUSB_GAMEPAD_START",
        "BACK": "XUSB_GAMEPAD_BACK",
        "DPAD_UP": "XUSB_GAMEPAD_DPAD_UP",
        "DPAD_DOWN": "XUSB_GAMEPAD_DPAD_DOWN",
        "DPAD_LEFT": "XUSB_GAMEPAD_DPAD_LEFT",
        "DPAD_RIGHT": "XUSB_GAMEPAD_DPAD_RIGHT",
    }

    def __init__(self) -> None:
        try:
            import vgamepad as vg
        except ImportError as exc:  # pragma: no cover - 実機依存
            raise RuntimeError(
                "vgamepad が未インストールです。'pip install vlm-auto-replay-tool[hid]' と"
                " ViGEmBus ドライバのインストールが必要です。"
            ) from exc
        self._vg = vg
        self._gamepad = vg.VX360Gamepad()

    def press(self, button: str, hold_ms: int) -> None:
        attr_name = self._BUTTON_ATTR_MAP.get(button)
        if attr_name is None:
            raise ValueError(f"未知のパッドボタンです: {button}")
        btn = getattr(self._vg.XUSB_BUTTON, attr_name)
        self._gamepad.press_button(button=btn)
        self._gamepad.update()
        time.sleep(hold_ms / 1000)
        self._gamepad.release_button(button=btn)
        self._gamepad.update()


# --- SendInput (ctypes) 定数・構造体 ---------------------------------------
_INPUT_KEYBOARD = 1
_INPUT_MOUSE = 0
_KEYEVENTF_KEYUP = 0x0002
_MOUSEEVENTF_MOVE = 0x0001

_VK_MAP = {
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "space": 0x20, "enter": 0x0D, "esc": 0x1B,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("union", _INPUT_UNION)]


class SendInputBackend:
    """SendInput経由のキーボード・マウス操作(Windows専用)。"""

    def __init__(self) -> None:
        if sys.platform != "win32":  # pragma: no cover - 実機依存
            raise RuntimeError("SendInputBackendはWindows専用です。")
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    def key(self, key: str, hold_ms: int) -> None:
        vk = _VK_MAP.get(key.lower())
        if vk is None:
            raise ValueError(f"未知のキーです: {key}")
        self._send_key(vk, key_up=False)
        time.sleep(hold_ms / 1000)
        self._send_key(vk, key_up=True)

    def mouse_move(self, dx: int, dy: int) -> None:
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(dx, dy, 0, _MOUSEEVENTF_MOVE, 0, None)
        self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def _send_key(self, vk: int, key_up: bool) -> None:
        inp = _INPUT(type=_INPUT_KEYBOARD)
        flags = _KEYEVENTF_KEYUP if key_up else 0
        inp.union.ki = _KEYBDINPUT(vk, 0, flags, 0, None)
        self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


# ---- Null実装(テスト・未接続環境向け) -----------------------------------


class NullPadBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def press(self, button: str, hold_ms: int) -> None:
        self.calls.append((button, hold_ms))


class NullKeyboardMouseBackend:
    def __init__(self) -> None:
        self.key_calls: list[tuple[str, int]] = []
        self.move_calls: list[tuple[int, int]] = []

    def key(self, key: str, hold_ms: int) -> None:
        self.key_calls.append((key, hold_ms))

    def mouse_move(self, dx: int, dy: int) -> None:
        self.move_calls.append((dx, dy))


class ScriptedOcrBackend:
    """テスト用OCRバックエンド。region -> text の応答をあらかじめ登録する。"""

    def __init__(self, default_text: str = ""):
        self._default_text = default_text
        self.calls: list[tuple[int, int, int, int]] = []

    def read_text(self, region: tuple[int, int, int, int]) -> str:
        self.calls.append(region)
        return self._default_text


# ---- Primitiveの束 ---------------------------------------------------


_DIRECTION_KEY_MAP = {"up": "w", "down": "s", "left": "a", "right": "d"}


class ApiPrimitives:
    """パッド・キーボード・マウス・OCR・movementのPrimitiveをまとめる実行基盤。"""

    def __init__(self, pad: PadBackend, keyboard_mouse: KeyboardMouseBackend, ocr: OcrBackend):
        self._pad = pad
        self._km = keyboard_mouse
        self._ocr = ocr

    def pad_input(self, button: str, hold_ms: int) -> None:
        self._pad.press(button, hold_ms)

    def key_input(self, key: str, hold_ms: int) -> None:
        self._km.key(key, hold_ms)

    def mouse_move(self, dx: int, dy: int) -> None:
        self._km.mouse_move(dx, dy)

    def ocr(self, region: tuple[int, int, int, int]) -> str:
        return self._ocr.read_text(region)

    def movement(self, direction: str, duration_ms: int) -> None:
        key = _DIRECTION_KEY_MAP.get(direction)
        if key is None:
            raise ValueError(f"未知の移動方向です: {direction}")
        self.key_input(key, duration_ms)
