import socket
import sys

port = int(sys.argv[1])
# Denial happens before any DNS lookup or connection, so these hosts need not
# exist. Addresses come from the documentation ranges.
cases = (
    ("GET", "example.ru", 80),
    ("GET", "www.example.su", 80),
    ("GET", "example.xn--p1ai", 80),
    ("CONNECT", "example.ru", 443),
    ("GET", "192.0.2.1", 80),
    ("GET", "[2001:db8::1]", 80),
    ("GET", "3221225985", 80),
    ("CONNECT", "192.0.2.1", 443),
    ("CONNECT", "[2001:db8::1]", 443),
)
failed = False
for method, host, target_port in cases:
    if method == "CONNECT":
        request = f"CONNECT {host}:{target_port} HTTP/1.1\r\nHost: {host}:{target_port}\r\n\r\n"
    else:
        request = f"GET http://{host}/ HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=30) as connection:
        connection.sendall(request.encode("ascii"))
        status_line = connection.makefile("rb").readline().decode("ascii", "replace")
    status = status_line.split(" ", 2)[1] if status_line.count(" ") >= 1 else "none"
    print(f"{method} {host}: {status}")
    if status != "403":
        failed = True
sys.exit(1 if failed else 0)
