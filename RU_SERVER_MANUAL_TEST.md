# Local ru-server Verification

This guide describes how to manually test the proxy chain through `ru-server` from your local machine.

Because `ru-server` drops all outbound internet traffic by default, a standard Outline client (which exposes a SOCKS5 proxy) cannot reach `youtube.com` directly. Instead, we use `ss-tunnel` to forward a local port through the VPN to `ru-server`'s local port `3128`. This port is itself forwarded by `ru-server` to `eu-server` (where the Squid HTTP proxy is listening).

The request path is:
`Local machine (curl)` -> `ss-tunnel (Outline)` -> `ru-server:3128` -> `ss-tunnel (Outline)` -> `eu-server:3128 (Squid)` -> `youtube.com`.

## Step 1: Start the ss-tunnel Docker Container

You will need the VPN access key for `ru-server` (usually starting with `ss://...`).

Run the following Python script in your terminal. It will prompt you for your `ss://` key, parse the credentials, and automatically start the `ss-tunnel` Docker container listening on your local port 3128:

```bash
python3 -c '
import base64, os
from urllib.parse import urlsplit, unquote

access_url = input("Enter your ss:// key: ").strip()
parts = urlsplit(access_url)
userinfo = unquote(parts.username)
padded = userinfo + "=" * (-len(userinfo) % 4)
method, password = base64.urlsafe_b64decode(padded).decode("utf-8").split(":", 1)

command = f"""docker run -d \\
  --name "local-ru-tunnel" \\
  --restart unless-stopped \\
  --network host \\
  --entrypoint ss-tunnel \\
  "shadowsocks/shadowsocks-libev:latest" \\
  -s {parts.hostname} -p {parts.port} -m {method} -k {password} \\
  -b 127.0.0.1 -l 3128 -L 127.0.0.1:3128"""

print("\nStarting ss-tunnel container with command:\n" + command + "\n")
os.system(command)
'
```

You can check the container logs to ensure the tunnel started correctly:
```bash
docker logs local-ru-tunnel
```

## Step 2: Run the Test Request (curl)

With local port 3128 forwarding traffic to Squid on `eu-server` via `ru-server`, you can now send an HTTP GET request to `youtube.com`.

Use `curl` with the `--proxy` (or `-x`) parameter:

```bash
curl -v --max-time 10 -x http://127.0.0.1:3128 http://youtube.com/ -I
```

If successful, you should see an HTTP response from YouTube (typically a 301 redirecting to `https://www.youtube.com/`).

## Step 3: Cleanup

After a successful test, stop and remove the container:

```bash
docker rm -f local-ru-tunnel
```
