import socket

def test_dns():
    try:
        host = "rabbitmq"
        print(f"Testing DNS resolution for {host}")
        ip = socket.gethostbyname(host)
        print(f"{host} resolved to {ip}")
    except Exception as e:
        print(f"DNS resolution failed: {e}")

if __name__ == "__main__":
    test_dns()