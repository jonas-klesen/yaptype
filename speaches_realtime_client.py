from __future__ import annotations

import base64
import inspect
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlencode, urlparse

import websockets


def speaches_http_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    # People often set OpenAI-style base URLs like "http://host:8000/v1". Normalize that.
    if base_path in ("", "/v1"):
        base_path = ""

    if parsed.scheme in ("ws", "wss"):
        scheme = "https" if parsed.scheme == "wss" else "http"
    else:
        scheme = parsed.scheme or "http"

    return f"{scheme}://{parsed.netloc}{base_path}".rstrip("/")


def speaches_ws_url(
    base_url: str,
    *,
    model: str,
    intent: str = "transcription",
    language: str | None = None,
    transcription_model: str | None = None,
) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    # People often set OpenAI-style base URLs like "http://host:8000/v1". Normalize that.
    if base_path in ("", "/v1"):
        base_path = ""

    if parsed.scheme in ("ws", "wss"):
        # Allow passing a websocket base URL directly.
        base = f"{parsed.scheme}://{parsed.netloc}{base_path}".rstrip("/")
    else:
        scheme = "wss" if parsed.scheme == "https" else "ws"
        base = f"{scheme}://{parsed.netloc}{base_path}".rstrip("/")

    query: dict[str, str] = {"model": model, "intent": intent}
    if language:
        query["language"] = language
    if transcription_model:
        query["transcription_model"] = transcription_model

    return f"{base}/v1/realtime?{urlencode(query)}"


@dataclass(frozen=True)
class SpeachesConnectionOptions:
    ws_url: str
    api_key: str | None = None
    open_timeout_s: float = 5.0
    ping_interval_s: float = 20.0
    ping_timeout_s: float = 20.0


class SpeachesRealtimeConnection:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self.session = _SessionProxy(self)
        self.input_audio_buffer = _InputAudioBufferProxy(self)

    async def _send_event(self, event: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(event))

    async def recv_event(self) -> dict[str, Any]:
        msg = await self._ws.recv()
        return json.loads(msg)

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[dict[str, Any]]:
        async for msg in self._ws:
            yield json.loads(msg)

    async def close(self) -> None:
        await self._ws.close()


class _SessionProxy:
    def __init__(self, conn: SpeachesRealtimeConnection) -> None:
        self._conn = conn

    async def update(self, *, session: dict[str, Any]) -> None:
        await self._conn._send_event({"type": "session.update", "session": session})


class _InputAudioBufferProxy:
    def __init__(self, conn: SpeachesRealtimeConnection) -> None:
        self._conn = conn

    async def append(self, *, audio: str) -> None:
        await self._conn._send_event({"type": "input_audio_buffer.append", "audio": audio})

    async def append_pcm16(self, *, audio_bytes: bytes) -> None:
        await self.append(audio=base64.b64encode(audio_bytes).decode("ascii"))

    async def commit(self) -> None:
        await self._conn._send_event({"type": "input_audio_buffer.commit"})

    async def clear(self) -> None:
        await self._conn._send_event({"type": "input_audio_buffer.clear"})


def speaches_ws_connect(opts: SpeachesConnectionOptions):
    kwargs: dict[str, object] = {
        "open_timeout": opts.open_timeout_s,
        "ping_interval": opts.ping_interval_s,
        "ping_timeout": opts.ping_timeout_s,
    }
    if opts.api_key:
        headers = {"Authorization": f"Bearer {opts.api_key}"}
        # websockets renamed `extra_headers` -> `additional_headers` in newer versions.
        params = inspect.signature(websockets.connect).parameters
        if "additional_headers" in params:
            kwargs["additional_headers"] = headers
        else:
            kwargs["extra_headers"] = headers
    return websockets.connect(opts.ws_url, **kwargs)

