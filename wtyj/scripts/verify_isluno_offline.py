"""Run Isluno unittest checks with sockets/DNS disabled and synthetic config."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "wtyj"))

    def denied(*args, **kwargs):
        raise RuntimeError("External network disabled for Isluno verification")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    with tempfile.TemporaryDirectory(prefix="isluno-offline-") as directory:
        config = Path(directory) / "client.json"
        config.write_text(json.dumps({"slug": "mermaid", "business": {"slug": "mermaid", "name": "Synthetic Isluno"},
                                      "features": {}, "channel_account_allowlist": {"mode": "strict", "zernio_accounts": ["fixture-account"]}}))
        os.environ["CLIENT_CONFIG_PATH"] = str(config)
        os.environ["ANTHROPIC_API_KEY"] = "offline-dummy-key"
        tests = unittest.defaultTestLoader.discover(str(root / "wtyj/tests/isluno"), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        print("External sockets/DNS denied. No legacy pytest root fixtures loaded.")
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
