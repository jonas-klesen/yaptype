import os
import socket

SOCKET_PATH = f"/run/user/{os.getuid()}/lissen_socket"


def send_command(command: str, *, socket_path: str = SOCKET_PATH) -> None:
    if not os.path.exists(socket_path):
        raise FileNotFoundError(f"Service socket not found: {socket_path}")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(socket_path)
        client.sendall(command.encode("utf-8"))
    finally:
        client.close()

