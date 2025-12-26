import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import sounddevice as sd
import websockets

from speaches_realtime_client import (
    SpeachesConnectionOptions,
    SpeachesRealtimeConnection,
    speaches_http_base_url,
    speaches_ws_connect,
    speaches_ws_url,
)

# --- CONFIGURATION ---
# Output folder for transcriptions
OUTPUT_DIR = os.path.expanduser("~") + "/transcriptions/" 
# Editor command (split into list for subprocess)
EDITOR_CMD = ["gnome-text-editor", "-n"]
# Output viewing mode:
# - "editor": open the file in EDITOR_CMD after transcription finishes (default / previous behavior)
# - "vim": open a terminal with vim tailing the file while transcription runs
# - "gui": open a minimal GUI window tailing the file while transcription runs
OUTPUT_VIEW_MODE = os.environ.get("YAPTYPE_VIEW_MODE", "gui").strip().lower()

# VIM mode launcher. Must open a terminal emulator when running as a systemd user service.
# Example alternatives:
# - ["xterm", "-e"]
# - ["kitty", "-e"]
# - ["alacritty", "-e"]
VIM_TERMINAL_CMD = ["gnome-terminal", "--"]

# When tailing (vim/gui), commit the audio buffer periodically to produce incremental segments.
# Set to 0 to disable and only produce a single final transcription at stop.
TAIL_SEGMENT_COMMIT_INTERVAL_S = float(os.environ.get("YAPTYPE_TAIL_SEGMENT_SECONDS", "5.0"))

# Ensure the tailed output file updates immediately on disk.
FSYNC_EACH_WRITE = os.environ.get("YAPTYPE_FSYNC_EACH_WRITE", "1").strip() not in ("0", "false", "False")
# Socket for communication
SOCKET_PATH = f"/run/user/{os.getuid()}/lissen_socket"

SPEACHES_BASE_URL = os.environ.get("SPEACHES_BASE_URL", "http://localhost:8000").rstrip("/")
SPEACHES_API_KEY = os.environ.get("SPEACHES_API_KEY")  # optional; speaches usually doesn't require one
TRANSCRIPTION_MODEL = os.environ.get("TRANSCRIPTION_MODEL", "Systran/faster-whisper-base.en")
TRANSCRIPTION_LANGUAGE = os.environ.get("TRANSCRIPTION_LANGUAGE")  # optional ISO-639-1 (e.g. "en")

# Speaches Realtime API expects 24kHz PCM16 mono audio.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
DTYPE = "int16"
MINIMUM_AUDIO_CHUNK_SIZE = 1024  # frames

# UX / logging
PRINT_PARTIALS = True  # print completed segment transcripts while recording
POST_STOP_TRANSCRIPTION_TIMEOUT_S = 5 * 60  # max wait after stop for transcript
SPEACHES_CONNECT_TIMEOUT_S = 5.0
SPEACHES_WARMUP_TIMEOUT_S = 30.0
SPEACHES_WARMUP_RETRIES = 60  # ~60 seconds total with 1s sleep
SPEACHES_MODEL_DOWNLOAD_TIMEOUT_S = 60 * 30  # 30 minutes (first run can take a while)
SPEACHES_MODEL_INSTALL_RETRIES = 60  # retry connecting to Speaches for ~60s


def _http_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None = None,
    timeout_s: float,
) -> tuple[int, str]:
    req = Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (local service call)
        body = resp.read().decode("utf-8", errors="replace")
        return int(resp.status), body


@dataclass
class _RecordingSession:
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    transcript_parts: list[str] = field(default_factory=list)
    error: BaseException | None = None
    output_path: str | None = None
    _writer: "_TranscriptFileWriter | None" = None


class _TranscriptFileWriter:
    def __init__(self, filepath: str) -> None:
        self._lock = threading.Lock()
        self._file = open(filepath, "a", encoding="utf-8")
        self._first = True

    def append_segment(self, segment: str) -> None:
        segment = segment.strip()
        if not segment:
            return
        with self._lock:
            if self._first:
                self._file.write(segment)
                self._first = False
            else:
                self._file.write(" " + segment)
            self._file.flush()
            if FSYNC_EACH_WRITE:
                os.fsync(self._file.fileno())

    def close(self) -> None:
        with self._lock:
            try:
                self._file.flush()
                if FSYNC_EACH_WRITE:
                    os.fsync(self._file.fileno())
            finally:
                self._file.close()


class TranscriptionServer:
    def __init__(self):
        self.recording = False
        self.processing = False
        self.server_socket = None
        self._session: _RecordingSession | None = None

        self._http_base_url = speaches_http_base_url(SPEACHES_BASE_URL)
        self._ws_url = speaches_ws_url(
            SPEACHES_BASE_URL,
            model=TRANSCRIPTION_MODEL,
            intent="transcription",
            language=TRANSCRIPTION_LANGUAGE,
        )
        print(f"[{datetime.now().time()}] Speaches websocket URL: {self._ws_url}")
        print(f"[{datetime.now().time()}] Transcription model: {TRANSCRIPTION_MODEL}")

        if OUTPUT_VIEW_MODE not in ("editor", "vim", "gui"):
            raise ValueError(f"Invalid YAPTYPE_VIEW_MODE={OUTPUT_VIEW_MODE!r} (expected: editor|vim|gui)")

        print(f"[{datetime.now().time()}] Ensuring Speaches model is installed...")
        self._ensure_speaches_model_installed()
        print("Model installed.")

        # Warm up speaches at service start so the first recording is instant.
        print(f"[{datetime.now().time()}] Warming up Speaches (loads model into RAM)...")
        asyncio.run(self._warmup_speaches())
        print("Warmup done. Ready to serve.")

    def _ws_headers(self) -> dict[str, str]:
        if not SPEACHES_API_KEY:
            return {}
        return {"Authorization": f"Bearer {SPEACHES_API_KEY}"}

    def _http_headers(self) -> dict[str, str]:
        # Speaches uses the same API key for http + websocket when configured.
        return self._ws_headers()

    def _ensure_speaches_model_installed(self) -> None:
        # Speaches provides: POST /v1/models/{model_id:path}
        # This blocks until the model is downloaded (200) or already exists (201).
        url = f"{self._http_base_url}/v1/models/{TRANSCRIPTION_MODEL}"
        last_error: BaseException | None = None
        for attempt in range(1, SPEACHES_MODEL_INSTALL_RETRIES + 1):
            try:
                status, body = _http_request(
                    url,
                    method="POST",
                    headers=self._http_headers(),
                    timeout_s=SPEACHES_MODEL_DOWNLOAD_TIMEOUT_S,
                )
                if status in (200, 201):
                    return
                raise RuntimeError(f"Unexpected status {status} from {url}: {body}")
            except HTTPError as e:
                # Authentication/config errors should fail fast.
                if e.code in (401, 403):
                    detail = ""
                    try:
                        detail = e.read().decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        pass
                    raise RuntimeError(
                        f"Speaches refused model download request (HTTP {e.code}). "
                        f"Check SPEACHES_API_KEY / Speaches API key config. {detail}".strip()
                    ) from e

                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
                last_error = RuntimeError(f"HTTP {e.code} {e.reason} {detail}".strip())
            except URLError as e:
                last_error = RuntimeError(f"Failed to reach Speaches at {self._http_base_url}: {e}")

            print(f">> Speaches model install retry {attempt}/{SPEACHES_MODEL_INSTALL_RETRIES}: {last_error}")
            time.sleep(1)

        raise RuntimeError(f"Speaches model install failed: {last_error}")

    def _ws_connect(self):
        return speaches_ws_connect(
            SpeachesConnectionOptions(
                ws_url=self._ws_url,
                api_key=SPEACHES_API_KEY,
                open_timeout_s=SPEACHES_CONNECT_TIMEOUT_S,
            )
        )

    async def _warmup_speaches(self) -> None:
        silence_bytes = b"\x00" * (SAMPLE_RATE * SAMPLE_WIDTH)  # 1s of silence
        last_error: BaseException | None = None
        for attempt in range(1, SPEACHES_WARMUP_RETRIES + 1):
            try:
                async with self._ws_connect() as ws:
                    conn = SpeachesRealtimeConnection(ws)
                    await conn.session.update(session={"turn_detection": None})
                    await conn.input_audio_buffer.append_pcm16(audio_bytes=silence_bytes)
                    await conn.input_audio_buffer.commit()

                    deadline = time.monotonic() + SPEACHES_WARMUP_TIMEOUT_S
                    while time.monotonic() < deadline:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5)
                        except TimeoutError:
                            continue
                        event = json.loads(msg)
                        et = event.get("type")
                        if et == "conversation.item.input_audio_transcription.completed":
                            return
                        if et == "error":
                            # Even if warmup errors, the connection worked; surface details and continue.
                            raise RuntimeError(event.get("error", {}).get("message") or "Speaches warmup error")
                    raise TimeoutError("Timed out waiting for Speaches warmup transcription")
            except BaseException as e:  # noqa: BLE001
                last_error = e
                print(f">> Speaches warmup retry {attempt}/{SPEACHES_WARMUP_RETRIES}: {e}")
                await asyncio.sleep(1)

        raise RuntimeError(f"Speaches warmup failed: {last_error}")

    def start_recording(self):
        if self.processing:
            print(">> Busy transcribing previous recording; ignoring start.")
            return
        if self.recording:
            return

        print(">> Starting recording (realtime via Speaches)...")
        self.recording = True

        session = _RecordingSession()
        self._session = session
        session.output_path = self._new_output_filepath()
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        # Create/truncate file immediately so tailing viewers can open it right away.
        with open(session.output_path, "w", encoding="utf-8"):
            pass
        session._writer = _TranscriptFileWriter(session.output_path)

        if OUTPUT_VIEW_MODE == "vim":
            self._open_vim_tail(session.output_path)
        elif OUTPUT_VIEW_MODE == "gui":
            self._open_gui_tail(session.output_path)

        def runner() -> None:
            try:
                asyncio.run(self._realtime_transcribe(session))
            except BaseException as e:  # noqa: BLE001
                session.error = e
            finally:
                try:
                    if session._writer is not None:
                        session._writer.close()
                except Exception:  # noqa: BLE001
                    pass
                session.done.set()

        threading.Thread(target=runner, daemon=True).start()

    def stop_recording(self):
        if not self.recording:
            return
        print(">> Stopping recording...")
        self.recording = False

        session = self._session
        if session is not None:
            session.stop.set()

        if self.processing:
            return
        self.processing = True
        threading.Thread(target=self._finalize_session, args=(session,), daemon=True).start()

    def _finalize_session(self, session: _RecordingSession | None) -> None:
        try:
            if session is None:
                print("No recording session.")
                return
            session.done.wait()
            if session.error is not None:
                print(f">> Recording session failed: {session.error}")

            text = " ".join(t.strip() for t in session.transcript_parts if t.strip()).strip()
            if not text:
                if session.output_path is not None and os.path.exists(session.output_path):
                    try:
                        if os.path.getsize(session.output_path) == 0:
                            os.remove(session.output_path)
                    except Exception:  # noqa: BLE001
                        pass
                print("No transcription produced.")
                return

            print(f"Result: {text}")
            if OUTPUT_VIEW_MODE == "editor" and session.output_path is not None:
                self._open_in_editor(session.output_path)
        finally:
            self._session = None
            self.processing = False

    async def _realtime_transcribe(self, session: _RecordingSession) -> None:
        print(f">> Connecting to Speaches Realtime API: {self._ws_url}")

        async with self._ws_connect() as ws:
            conn = SpeachesRealtimeConnection(ws)
            await conn.session.update(session={"turn_detection": None})

            committed_item_id: str | None = None
            pending_item_ids: set[str] = set()
            progress = asyncio.Event()
            commit_count = 0
            completed_count = 0
            transcript_count = 0
            last_error: str | None = None

            async def receiver() -> None:
                nonlocal committed_item_id
                nonlocal last_error
                nonlocal commit_count
                nonlocal completed_count
                nonlocal transcript_count
                try:
                    async for event in conn:
                        et = event.get("type")
                        if et == "input_audio_buffer.committed":
                            item_id = event.get("item_id")
                            if isinstance(item_id, str):
                                committed_item_id = item_id
                                pending_item_ids.add(item_id)
                                commit_count += 1
                                progress.set()
                        elif et == "conversation.item.input_audio_transcription.completed":
                            item_id = event.get("item_id")
                            if isinstance(item_id, str) and pending_item_ids and item_id not in pending_item_ids:
                                continue
                            transcript = (event.get("transcript") or "").strip()
                            if transcript:
                                transcript_count += 1
                                session.transcript_parts.append(transcript)
                                if session._writer is not None:
                                    session._writer.append_segment(transcript)
                                if PRINT_PARTIALS:
                                    print(f">> Partial: {transcript}")
                            if isinstance(item_id, str) and item_id in pending_item_ids:
                                pending_item_ids.discard(item_id)
                                completed_count += 1
                            progress.set()
                        elif et == "error":
                            err = event.get("error") or {}
                            message = err.get("message") or json.dumps(err)
                            last_error = message
                            session.error = RuntimeError(message)
                            print(f">> Speaches error: {message}")
                            break
                except websockets.exceptions.ConnectionClosedError:
                    pass
                finally:
                    pass

            async def sender() -> None:
                stream = sd.InputStream(channels=CHANNELS, samplerate=SAMPLE_RATE, dtype=DTYPE)
                stream.start()
                commit_interval_s = 0.0
                if OUTPUT_VIEW_MODE in ("vim", "gui"):
                    commit_interval_s = max(0.0, TAIL_SEGMENT_COMMIT_INTERVAL_S)
                last_commit_at = time.monotonic()
                wrote_any_audio = False
                try:
                    while not session.stop.is_set():
                        if stream.read_available < MINIMUM_AUDIO_CHUNK_SIZE:
                            await asyncio.sleep(0)
                            continue

                        data, _ = stream.read(MINIMUM_AUDIO_CHUNK_SIZE)
                        bytes_data = data.tobytes()
                        assert len(bytes_data) == len(data) * SAMPLE_WIDTH

                        await conn.input_audio_buffer.append_pcm16(audio_bytes=bytes_data)
                        wrote_any_audio = True

                        if commit_interval_s > 0 and (time.monotonic() - last_commit_at) >= commit_interval_s:
                            try:
                                await conn.input_audio_buffer.commit()
                                last_commit_at = time.monotonic()
                            except Exception:  # noqa: BLE001
                                pass
                finally:
                    stream.stop()
                    stream.close()
                    try:
                        if wrote_any_audio:
                            await conn.input_audio_buffer.commit()
                    except Exception:  # noqa: BLE001
                        pass

            recv_task = asyncio.create_task(receiver())
            send_task = asyncio.create_task(sender())

            await send_task
            deadline = time.monotonic() + POST_STOP_TRANSCRIPTION_TIMEOUT_S
            while time.monotonic() < deadline:
                if last_error is not None:
                    break
                if commit_count > 0 and completed_count >= commit_count and not pending_item_ids:
                    break
                if commit_count == 0 and transcript_count > 0 and not pending_item_ids:
                    break
                progress.clear()
                try:
                    await asyncio.wait_for(progress.wait(), timeout=0.2)
                except TimeoutError:
                    pass
            if commit_count > completed_count or pending_item_ids:
                print(f">> Timed out waiting for transcription ({POST_STOP_TRANSCRIPTION_TIMEOUT_S}s).")

            if last_error is None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass
            await recv_task

    def _new_output_filepath(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"transcription_{timestamp}.txt"
        return os.path.join(OUTPUT_DIR, filename)

    def _open_in_editor(self, filepath: str) -> None:
        print(f"Saved to {filepath}")
        try:
            env = os.environ.copy()
            subprocess.Popen(EDITOR_CMD + [filepath], env=env)
        except Exception as e:  # noqa: BLE001
            print(f"Failed to open editor: {e}")

    def _open_vim_tail(self, filepath: str) -> None:
        # Tails by repeatedly checking for file changes and jumping to EOF.
        vim_cmd = [
            "vim",
            "-n",  # no swapfile
            "-R",  # read-only by default; :w! still possible if user really wants it
            "-c",
            "set autoread | set updatetime=200 | autocmd CursorHold,CursorHoldI * checktime | autocmd FileChangedShellPost * normal! G | normal! G | stopinsert",
            filepath,
        ]
        try:
            env = os.environ.copy()
            subprocess.Popen(VIM_TERMINAL_CMD + vim_cmd, env=env)
        except Exception as e:  # noqa: BLE001
            print(f"Failed to open vim tail: {e}")

    def _open_gui_tail(self, filepath: str) -> None:
        # Spawn GUI as a separate process so the systemd service can keep running.
        try:
            env = os.environ.copy()
            subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "tail_gui.py"), filepath], env=env)
        except Exception as e:  # noqa: BLE001
            print(f"Failed to open GUI tail: {e}")

    def run(self):
        # Ensure socket cleanup
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(SOCKET_PATH)
        self.server_socket.listen(1)
        
        print(f"Listening on {SOCKET_PATH}")

        try:
            while True:
                conn, _ = self.server_socket.accept()
                with conn:
                    data = conn.recv(1024)
                    command = data.decode('utf-8').strip()
                    
                    if command == "TOGGLE":
                        if self.recording:
                            self.stop_recording()
                        else:
                            self.start_recording()
                    elif command == "START":
                        self.start_recording()
                    elif command == "STOP":
                        self.stop_recording()
        finally:
            os.remove(SOCKET_PATH)

if __name__ == "__main__":
    # Flush stdout to make sure logs appear in journalctl immediately
    sys.stdout.reconfigure(line_buffering=True)
    server = TranscriptionServer()
    server.run()
