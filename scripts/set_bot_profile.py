"""Set Telegram bot name, descriptions, and profile photo from assets/bot-avatar.jpg."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
AVATAR = ROOT / "assets" / "bot-avatar.jpg"

NAME = "Домашняя кухня"
SHORT = "Блюда → состав → кладовая и покупки. Для двоих."
DESC = (
    "Домашняя кухня на двоих: блюда и заготовки, проверка состава, "
    "кладовая и список покупок. Пришлите JSON от ИИ-агента — бот разложит по полкам."
)


def api_json(token: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def set_profile_photo(token: str, path: Path) -> dict:
    boundary = "----HomeMealCraftBoundary"
    photo_bytes = path.read_bytes()
    meta = json.dumps({"type": "static", "photo": "attach://avatar"})

    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode())
        parts.append(b"\r\n")

    def file_part(name: str, filename: str, data: bytes, content_type: str) -> None:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
            ).encode()
        )
        parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        parts.append(data)
        parts.append(b"\r\n")

    field("photo", meta)
    file_part("avatar", path.name, photo_bytes, "image/jpeg")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyProfilePhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode(), "code": exc.code}


def main() -> None:
    load_dotenv(ROOT / ".env")
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    print("name", api_json(token, "setMyName", {"name": NAME}))
    print("short", api_json(token, "setMyShortDescription", {"short_description": SHORT}))
    print("desc", api_json(token, "setMyDescription", {"description": DESC}))
    if not AVATAR.exists():
        raise SystemExit(f"missing avatar: {AVATAR}")
    print("photo", set_profile_photo(token, AVATAR))


if __name__ == "__main__":
    main()
