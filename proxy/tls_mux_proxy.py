#!/usr/bin/env python3
from __future__ import annotations

import os
import select
import socket
import ssl
import threading


LISTEN_HOST = os.environ.get("SWARM_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("SWARM_PROXY_PORT", "8765"))
BACKEND_HOST = os.environ.get("SWARM_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("SWARM_BACKEND_PORT", "8764"))
CERT_FILE = os.environ.get("SWARM_TLS_CERT", "/etc/compute-swarm/tls/server.crt")
KEY_FILE = os.environ.get("SWARM_TLS_KEY", "/etc/compute-swarm/tls/server.key")


def pump(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 300)
            if errored:
                break
            if not readable:
                break
            for src in readable:
                dst = right if src is left else left
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except (OSError, ssl.SSLError):
        return
    finally:
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def handle(client: socket.socket, addr: tuple[str, int], context: ssl.SSLContext) -> None:
    try:
        first = client.recv(1, socket.MSG_PEEK)
        if first and first[0] == 0x16:
            client = context.wrap_socket(client, server_side=True)
        backend = socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=10)
        pump(client, backend)
    except (OSError, ssl.SSLError):
        try:
            client.close()
        except OSError:
            pass


def main() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(512)
    while True:
        client, addr = server.accept()
        thread = threading.Thread(target=handle, args=(client, addr, context), daemon=True)
        thread.start()


if __name__ == "__main__":
    main()
