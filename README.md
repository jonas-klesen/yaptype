Blog post: https://blog.jonas-klesen.de/yaptype

# YapType

Local speech-to-text on Linux.
Press a hotkey to start/stop recording; audio is streamed to a local Speaches server and you get one transcription (no VAD/segmentation; single-speaker assumed) opened in your editor.

## Requirements
- Python 3.12+
- Poetry
- Docker + Docker Compose (for Speaches)

## Install
```bash
# Debian/Ubuntu (adjust for your distro)
sudo apt install docker.io docker-compose-plugin

poetry install
```

## Configure
Defaults live in `server.py`. The main knobs:
- `OUTPUT_DIR` (default `~/transcriptions/`)
- `EDITOR_CMD` (default `["gnome-text-editor", "-n"]`)
- `SPEACHES_BASE_URL` (default `http://localhost:8000`)
- `SPEACHES_API_KEY` (optional)
- `TRANSCRIPTION_MODEL` (default `Systran/faster-distil-whisper-small.en`)
- `TRANSCRIPTION_LANGUAGE` (optional, e.g. `en`)

If Speaches warmup fails with `Not Found`, ensure `LOOPBACK_HOST_URL` is set (this repo does that in `compose.speaches.override.yaml`).

## Run as user services (recommended)
1) Copy the unit files:
```bash
cp speaches.service ~/.config/systemd/user/
cp yaptype.service ~/.config/systemd/user/
```

2) Edit them:
- In `~/.config/systemd/user/speaches.service`, set `WorkingDirectory` to your repo path.
- In `~/.config/systemd/user/yaptype.service`, set `ExecStart` to your Poetry venv python + this repo’s `server.py`.
  Get the python path with `poetry run which python`.

3) Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable --now speaches.service
systemctl --user enable --now yaptype.service
```

Optional (start services at boot even before login):
```bash
sudo loginctl enable-linger "$USER"
```

## Hotkey
Create a custom shortcut that runs:
`/path/to/poetry/venv/python /path/to/repo/client.py`

## Troubleshooting
- Logs: `journalctl --user -u yaptype.service -f`
- WebSocket 403: Speaches rejected the handshake (check `SPEACHES_BASE_URL`, Speaches is running, and `SPEACHES_API_KEY` if configured)
- Install model manually:
  - `curl -sS -X POST "http://localhost:8000/v1/models/Systran/faster-distil-whisper-small.en"`
