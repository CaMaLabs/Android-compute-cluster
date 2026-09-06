#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import sys


TARGET_URL = os.environ.get("SWARM_TUNNEL_TARGET", "http://127.0.0.1:8765")
URL_FILE = os.environ.get("SWARM_TUNNEL_URL_FILE", "/var/lib/compute-swarm/cloudflared-url.txt")
CLOUDFLARED = os.environ.get("CLOUDFLARED_BIN", "/usr/bin/cloudflared")


def main() -> int:
    os.makedirs(os.path.dirname(URL_FILE), exist_ok=True)
    proc = subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", TARGET_URL],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    pattern = re.compile(r"https://[-a-zA-Z0-9]+\.trycloudflare\.com")
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        match = pattern.search(line)
        if match:
            with open(URL_FILE, "w", encoding="utf-8") as handle:
                handle.write(match.group(0) + "\n")
    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
