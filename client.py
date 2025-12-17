import socket
import os
import sys

SOCKET_PATH = f"/run/user/{os.getuid()}/lissen_socket"

def main():
    if not os.path.exists(SOCKET_PATH):
        print("Error: Service is not running.")
        sys.exit(1)

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)
        client.sendall(b"TOGGLE")
        client.close()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    main()