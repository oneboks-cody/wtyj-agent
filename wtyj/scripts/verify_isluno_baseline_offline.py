"""Run the inherited SDK-boundary cache checks with no external network.

This is baseline verification, not a live/model/WhatsApp test or Isluno journey
acceptance. Run from any directory with the project's Python dependencies.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "wtyj/briefs/isluno_baseline_manifest.json").read_text())
    baseline = manifest["backend"]["frozen_base"]
    runtime_files = manifest["backend"]["runtime"]["python_files"]
    for item in runtime_files:
        content = subprocess.check_output(
            ["git", "show", baseline + ":wtyj/" + item["path"]], cwd=root,
        )
        assert len(content) == item["bytes"], item["path"]
        assert hashlib.sha256(content).hexdigest() == item["sha256"], item["path"]
    deps = {item["key"]: item["depends_on"] for item in manifest["dependency_map"]}
    visited = set()

    def visit(key, ancestors):
        assert key in deps and key not in ancestors, (key, ancestors)
        if key not in visited:
            for dependency in deps[key]:
                visit(dependency, ancestors | {key})
            visited.add(key)

    for key in deps:
        visit(key, set())
    fixture = root / "wtyj/tests/isluno/fixtures/contracts.v1.json"
    examples = json.loads(fixture.read_text())
    assert examples["schema_version"] == "isluno.v1" and examples["fixture_only"] is True
    assert len({p["id"] for p in examples["products"]}) == len(examples["products"])
    for quote in examples["quotes"]:
        assert quote["mode"] == "demo"
        assert all(line["amount_minor"] == line["quantity"] * line["unit_minor"] for line in quote["lines"])
        assert quote["total_minor"] == sum(line["amount_minor"] for line in quote["lines"])
        assert quote["payable_minor"] == quote["total_minor"]
    assert examples["demo_receipt"]["total_minor"] == examples["quotes"][1]["total_minor"]
    assert examples["demo_receipt"]["real_money_moved"] is False

    def denied(*_args, **_kwargs):
        raise RuntimeError("External network is disabled for Isluno baseline verification")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["ANTHROPIC_API_KEY"] = "offline-dummy-key"
    sys.path.insert(0, str(root / "wtyj"))
    with tempfile.TemporaryDirectory(prefix="isluno-baseline-") as directory:
        temp = Path(directory)
        config = temp / "synthetic-client.json"
        config.write_text(json.dumps({
            "slug": "mermaid",
            "business": {"slug": "mermaid", "name": "Synthetic Isluno baseline"},
            "features": {"mermaid_reservation_demo": True},
            "channel_account_allowlist": {"mode": "strict", "zernio_accounts": ["fixture-account"]},
        }))
        os.environ["CLIENT_CONFIG_PATH"] = str(config)
        import pytest

        path = root / "wtyj/tests/marina/test_mermaid_prompt_cache.py"
        print(f"{len(runtime_files)} baseline source hashes match; dependency graph and contract examples valid.")
        print("External sockets/DNS denied; synthetic config; legacy root fixtures excluded.")
        return pytest.main([
            "-q", "--confcutdir", str(path.parent), str(path),
            "-o", "cache_dir=" + str(temp / "pytest-cache"),
        ])


if __name__ == "__main__":
    raise SystemExit(main())
