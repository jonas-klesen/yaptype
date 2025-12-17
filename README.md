

# YapType

A lightweight, local yap-to-text tool for Linux. 
It listens for a global hotkey, records audio, transcribes it locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper), and opens the text in your preferred editor. The model is pre-loaded in RAM via a background service for instant recording.


## Requirements
- Python 3.12+
- [Poetry](https://python-poetry.org/)
- `ffmpeg` (required by whisper)

## Installation

1. **Install Dependencies**
    ```bash
    sudo apt install ffmpeg  # Debian/Ubuntu
    # sudo pacman -S ffmpeg  # Arch
    
    poetry install
    ```

2. **Configure (Optional)**
Open `server.py` to change defaults:
* `OUTPUT_DIR`: Where transcription text files are saved (Default: `~/transcriptions/`). If you don't want to preserve them, just set this to `/tmp/`
* `EDITOR_CMD`: The text editor to open (Default: `gnome-text-editor -n`).
* `MODEL_SIZE`: Whisper model size (Default: `base.en`).  For options, see [here](https://github.com/openai/whisper?tab=readme-ov-file#available-models-and-languages). I recommend base or small, or tiny if you have a bad cpu.


3. **Setup Background Service**
Edit `yaptype.service` and ensure the paths to `python` (inside poetry env) and `server.py` are correct. To get the python executable path, use `poetry run which python` or `poetry env info`.
```bash
cp yaptype.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now yaptype.service
```

*    If you change the yaptype.service, you need to rerun the first two commands and then do `systemctl --user restart yaptype.service` !


4. **Set Keyboard Shortcut**
Go to your System Settings -> Keyboard -> Shortcuts (or your Window Manager config).
Create a new custom shortcut:
* **Command:** `/path/to/poetry/venv/python /path/to/repo/client.py`

* --> This python path is the same as above.
* **Shortcut:** `Ctrl + Alt + -` (or your preference)

## Usage

1. Press your shortcut (e.g., `Ctrl + Alt + - `) to **start** recording. A microphone icon will light up in your taskbar
2. Speak your thought. You'll notice that the microphone privacy indicator lights up in the system menu (top-right corner) while the recording is running (on GNOME, at least).
3. Press the shortcut again to **stop**.
4. The transcription will process in the background and pop up in your text editor automatically.

## Troubleshooting

Check service logs if recording doesn't start or editor doesn't open:

```bash
journalctl --user -u yaptype.service -f
```

### Model storage path
The code downloads the model from HuggingFace on first start and caches it in `~/.cache/huggingface/hub/`:

`du -sh ~/.cache/huggingface/hub/models--Systran--faster-whisper*`


### Check memory usage

You can see the memory usage by using `systemctl --user status yaptype.service`. With the base model, about 250MB of RAM are used.