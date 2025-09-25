import os
import asyncio
from typing import Tuple

from pyrogram import Client

from config import API_ID, API_HASH, TG_SESSION_STRING, BRIDGE_CHANNEL_ID

_pyro_client: Client | None = None
_started = False
_lock = asyncio.Lock()


def _ensure_bridge_config():
    if not TG_SESSION_STRING or BRIDGE_CHANNEL_ID == 0:
        raise RuntimeError("Bridge not configured: set TG_SESSION_STRING and BRIDGE_CHANNEL_ID in .env")


async def _get_client() -> Client:
    global _pyro_client, _started
    _ensure_bridge_config()
    async with _lock:
        if _pyro_client is None:
            # name can be anything; session_string is used
            _pyro_client = Client(
                name="bridge",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=TG_SESSION_STRING,
                no_updates=True,
            )
        if not _started:
            await _pyro_client.start()
            _started = True
    return _pyro_client


def _is_video(filename: str) -> bool:
    fn = filename.lower()
    return any(fn.endswith(ext) for ext in (
        ".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".wmv"
    ))


async def upload_to_bridge(file_path: str, filename: str, caption: str | None = None) -> Tuple[int, int]:
    """
    Uploads the file to the bridge channel using the user account (Pyrogram)
    and returns (chat_id, message_id) of the uploaded message.
    """
    client = await _get_client()

    if _is_video(filename):
        msg = await client.send_video(
            chat_id=BRIDGE_CHANNEL_ID,
            video=file_path,
            caption=caption,
            supports_streaming=True,
        )
    else:
        msg = await client.send_document(
            chat_id=BRIDGE_CHANNEL_ID,
            document=file_path,
            caption=caption,
        )

    return BRIDGE_CHANNEL_ID, msg.id
