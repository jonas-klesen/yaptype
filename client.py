import sys

from control import SOCKET_PATH, send_command

def main():
    try:
        send_command("TOGGLE", socket_path=SOCKET_PATH)
    except FileNotFoundError:
        print("Error: Service is not running.")
        sys.exit(1)
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    main()
