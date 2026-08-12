from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "vlm_auto_replay.gui.server:app", host="127.0.0.1", port=8765, reload=False
    )


if __name__ == "__main__":
    main()
