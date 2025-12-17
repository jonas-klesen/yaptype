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
# Model Settings
MODEL_SIZE = "base.en"
COMPUTE_TYPE = "int8"
# Socket for communication
SOCKET_PATH = f"/run/user/{os.getuid()}/lissen_socket"

class TranscriptionServer:
    def __init__(self):
        self.recording = False
        self.audio_data = []
        self.fs = 16000
        self.server_socket = None

        print(f"[{datetime.now().time()}] Loading Whisper model ({MODEL_SIZE})...")
        self.model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE_TYPE)
        print("Model loaded. Ready to serve.")

    def start_recording(self):
        print(">> Starting recording...")
        self.recording = True
        self.audio_data = []
        # Use default input device
        self.stream = sd.InputStream(callback=self.audio_callback, channels=1, samplerate=self.fs)
        self.stream.start()

    def stop_recording(self):
        if not self.recording: return
        print(">> Stopping recording...")
        self.recording = False
        self.stream.stop()
        self.stream.close()
        
        # Process in a separate thread so we don't block the socket loop
        threading.Thread(target=self.process_audio).start()

    def audio_callback(self, indata, frames, time, status):
        if self.recording:
            self.audio_data.append(indata.copy())

    def process_audio(self):
        if not self.audio_data:
            print("No audio recorded.")
            return

        print(">> Transcribing...")
        # Concatenate audio chunks
        audio_np = np.concatenate(self.audio_data, axis=0).flatten()
        
        # Transcribe
        segments, info = self.model.transcribe(audio_np, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()
        
        print(f"Result: {text}")
        self.save_and_open(text)

    def save_and_open(self, text):
        if not text: return

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"transcription_{timestamp}.txt"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # Write to file
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