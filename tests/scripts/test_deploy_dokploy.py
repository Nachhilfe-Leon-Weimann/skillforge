"""The deploy script against an in-process fake of the Dokploy API and the service's /health."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "deploy-dokploy.sh"
API_KEY = "test-key-123"

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("bash", "curl", "jq")),
    reason="the deploy script needs bash, curl and jq",
)


@dataclass
class FakeDokploy:
    final_status: str = "done"
    polls_until_final: int = 2
    health_version: str = "1.2.3"
    deployments: list[dict[str, str]] = field(default_factory=list)
    deploy_calls: list[dict[str, Any]] = field(default_factory=list)
    health_calls: int = 0
    polls: int = 0

    def advance(self) -> None:
        """Each listing moves the new deployment one step closer to its final status."""
        if not self.deployments or self.deployments[0]["deploymentId"] != "dep-new":
            return
        self.polls += 1
        if self.polls >= self.polls_until_final:
            self.deployments[0]["status"] = self.final_status
            if self.final_status == "error":
                self.deployments[0]["errorMessage"] = "migrate exited with code 1"


def _handler(state: FakeDokploy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, code: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if self.headers.get("x-api-key") == API_KEY:
                return True
            self._send(401, {"message": "Unauthorized"})
            return False

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                state.health_calls += 1
                self._send(200, {"status": "ok", "version": state.health_version})
            elif not self._authorized():
                return
            elif path == "/api/deployment.allByCompose":
                state.advance()
                self._send(200, state.deployments)
            else:
                self._send(404, {"message": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if urlparse(self.path).path != "/api/compose.deploy":
                self._send(404, {"message": "not found"})
                return
            state.deploy_calls.append(payload)
            state.deployments.insert(0, {"deploymentId": "dep-new", "status": "running", "errorMessage": ""})
            self._send(200, {"success": True})

    return Handler


@pytest.fixture
def dokploy() -> Iterator[tuple[FakeDokploy, str]]:
    state = FakeDokploy(deployments=[{"deploymentId": "dep-old", "status": "done", "errorMessage": ""}])
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _run(base_url: str, *, health: bool = True, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "DOKPLOY_BASE_URL": base_url,
        "DOKPLOY_API_KEY": API_KEY,
        "DOKPLOY_COMPOSE_ID": "compose-1",
        "RELEASE_VERSION": "1.2.3",
        "HEALTH_URL": f"{base_url}/health" if health else "",
        "DOKPLOY_DEPLOY_TIMEOUT_SECONDS": "5",
        "DOKPLOY_HEALTH_TIMEOUT_SECONDS": "2",
        "DOKPLOY_POLL_INTERVAL_SECONDS": "0.1",
        **overrides,
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False)


def test_deploys_waits_and_verifies_the_version(dokploy):
    state, base_url = dokploy

    result = _run(base_url)

    assert result.returncode == 0, result.stderr
    assert [call["composeId"] for call in state.deploy_calls] == ["compose-1"]
    assert "1.2.3" in state.deploy_calls[0]["title"]
    assert "(answer: success)" in result.stdout
    assert state.polls >= state.polls_until_final
    assert state.health_calls >= 1
    assert API_KEY not in result.stdout + result.stderr


def test_reports_the_deployment_to_github_actions(dokploy, tmp_path):
    _, base_url = dokploy
    output, summary = tmp_path / "output", tmp_path / "summary"

    result = _run(base_url, GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary))

    assert result.returncode == 0, result.stderr
    assert output.read_text() == "deployment_id=dep-new\n"
    assert "| Version | v1.2.3 |" in summary.read_text()
    assert "| Dokploy deployment | dep-new |" in summary.read_text()


def test_a_failed_deployment_fails_the_script_with_dokploys_message(dokploy):
    state, base_url = dokploy
    state.final_status = "error"

    result = _run(base_url)

    assert result.returncode != 0
    assert "migrate exited with code 1" in result.stderr
    assert state.health_calls == 0


def test_an_old_version_on_health_fails_after_the_timeout(dokploy):
    state, base_url = dokploy
    state.health_version = "1.2.2"

    result = _run(base_url)

    assert result.returncode != 0
    assert "1.2.3" in result.stderr
    assert state.health_calls >= 1


def test_refuses_to_start_while_another_deployment_is_running(dokploy):
    state, base_url = dokploy
    state.deployments[0]["status"] = "running"

    result = _run(base_url)

    assert result.returncode != 0
    assert "already running" in result.stderr
    assert state.deploy_calls == []


def test_without_a_health_url_the_deployment_status_is_enough(dokploy):
    state, base_url = dokploy

    result = _run(base_url, health=False)

    assert result.returncode == 0, result.stderr
    assert state.health_calls == 0


def test_dry_run_only_reads(dokploy):
    state, base_url = dokploy

    result = _run(base_url, DRY_RUN="true")

    assert result.returncode == 0, result.stderr
    assert state.deploy_calls == []


def test_a_wrong_api_key_is_reported(dokploy):
    _, base_url = dokploy

    result = _run(base_url, DOKPLOY_API_KEY="wrong")

    assert result.returncode != 0
    assert "HTTP 401" in result.stderr


def test_a_plain_http_dokploy_url_is_rejected():
    result = _run("http://dokploy.example.com")

    assert result.returncode != 0
    assert "HTTPS" in result.stderr
