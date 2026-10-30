import socket
import os
import sys
import threading
import subprocess
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
from datetime import datetime
from faster_whisper import WhisperModel

# --- CONFIGURATION ---
# Output folder for transcriptions
OUTPUT_DIR = os.path.expanduser("~") + "/transcriptions/" 
# Editor command (split into list for subprocess)
EDITOR_CMD = ["gnome-text-editor", "-n"]
# Clipboard commands, tried in order until one works (wl-copy fails outside Wayland)
CLIPBOARD_CMDS = [["wl-copy"], ["xclip", "-selection", "clipboard"]]
# Model Settings
MODEL_SIZE = "medium.en"
COMPUTE_TYPE = "int8"
# Socket for communication
SOCKET_PATH = f"/run/user/{os.getuid()}/lissen_socket"

class TranscriptionServer:
    def __init__(self):
        self.recording = False
        self.audio_data = []
        self.fs = 16000
        self.server_socket = None
        self.recording_start_time = None
        self.last_callback_time = None
        self.callback_count = 0

        print(f"[{datetime.now().time()}] Loading Whisper model ({MODEL_SIZE})...")
        self.model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
        print("Model loaded. Ready to serve.")

    def start_recording(self):
        print(">> Starting recording...")
        self.recording = True
        self.audio_data = []
        self.callback_count = 0
        self.recording_start_time = datetime.now()
        self.last_callback_time = self.recording_start_time
        # Use default input device
        self.stream = sd.InputStream(callback=self.audio_callback, channels=1, samplerate=self.fs)
        self.stream.start()

    def stop_recording(self):
        if not self.recording: return
        recording_duration = (datetime.now() - self.recording_start_time).total_seconds()
        print(f">> Stopping recording... (wall time: {recording_duration:.2f}s, callbacks: {self.callback_count})")
        self.recording = False
        self.stream.stop()
        self.stream.close()

        # Process in a separate thread so we don't block the socket loop
        threading.Thread(target=self.process_audio).start()

    def audio_callback(self, indata, frames, time, status):
        if status:
            print(f"   [callback status: {status}]")
        if self.recording:
            self.callback_count += 1
            self.last_callback_time = datetime.now()
            self.audio_data.append(indata.copy())

    def process_audio(self):
        if not self.audio_data:
            print("No audio recorded.")
            return

        print(">> Transcribing...")
        # Concatenate audio chunks
        audio_np = np.concatenate(self.audio_data, axis=0).flatten()

        # Diagnostic info
        audio_duration = len(audio_np) / self.fs
        print(f"   Audio chunks: {len(self.audio_data)}, samples: {len(audio_np)}, duration: {audio_duration:.2f}s")

        # Transcribe
        segments, info = self.model.transcribe(audio_np, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()

        print(f"Result: {text}")
        self.save_and_open(text)

    def save_and_open(self, text):
        if not text: return

        self.copy_to_clipboard(text)

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"transcription_{timestamp}.txt"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # Write to file
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)
        
        print(f"Saved to {filepath}")

        # Open in Editor
        try:
            # We copy the current environment to ensure the editor has display access
            env = os.environ.copy()
            subprocess.Popen(EDITOR_CMD + [filepath], env=env)
        except Exception as e:
            print(f"Failed to open editor: {e}")

    def copy_to_clipboard(self, text):
        for cmd in CLIPBOARD_CMDS:
            try:
                # These tools stay in the background to serve the clipboard, so they must not hold our pipes
                subprocess.run(cmd, input=text.encode("utf-8"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                continue
            print(f"Copied to clipboard with {cmd[0]}")
            return
        print("Failed to copy to clipboard: install wl-clipboard or xclip")

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
        finally:
            os.remove(SOCKET_PATH)

if __name__ == "__main__":
    # Flush stdout to make sure logs appear in journalctl immediately
    sys.stdout.reconfigure(line_buffering=True)
    server = TranscriptionServer()
    server.run()