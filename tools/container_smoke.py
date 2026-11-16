"""Isolated Docker smoke: synthetic inputs only, unique disposable project/volume.

Run: python tools/container_smoke.py sha256:<built image ID>
Never accepts a mutable tag, an existing volume, a host or a network override.
"""

import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICE = "192.0.2.10"  # documentation range: the stand-in office-network address


def command(*args: str, source: str | None = None, env=None) -> str:
    result = subprocess.run(
        args, input=source, text=True, capture_output=True, cwd=ROOT, env=env, check=False
    )
    if result.returncode:
        # Do not print command environments, private logs or response payloads.
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}): {result.stderr}")
    return result.stdout.strip()


def smoke(image: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Use an immutable local image ID or registry digest, not a tag.")
    project = "drives-smoke-" + secrets.token_hex(6)
    api_secret = "synthetic-" + secrets.token_hex(16)
    env = dict(
        os.environ,
        DRIVES_IMAGE=image,
        DRIVES_PORT="0",
        DRIVES_SESSION_SECRET=secrets.token_hex(32),
        T6_API_URL="http://127.0.0.1:9",  # nothing listens: the app must say Unavailable, not fail
        T6_API_SECRET=api_secret,
        DRIVES_ALLOWED_HOST=OFFICE,
    )
    env.pop("DRIVES_BIND", None)
    compose = ("docker", "compose", "-p", project)

    def bind_env(bind: str) -> dict[str, str]:
        return {"DRIVES_BIND": bind} if bind else {}

    volume = project + "_drives_data"

    def dc(*args):
        return command(*compose, *args, env=env)

    def target():
        container = dc("ps", "-q", "app")
        data = json.loads(command("docker", "inspect", container))[0]
        bindings = data["NetworkSettings"]["Ports"]["8080/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        assert data["Config"]["User"] == "10001:10001"
        assert data["HostConfig"]["ReadonlyRootfs"]
        assert data["HostConfig"]["CapDrop"] == ["ALL"]
        assert any(
            m["Name"] == volume and m["Destination"] == "/data"
            for m in data["Mounts"]
            if m["Type"] == "volume"
        )
        expected = json.loads(command("docker", "image", "inspect", image))[0]["Id"]
        assert data["Image"] == expected
        url = "http://127.0.0.1:" + bindings[0]["HostPort"]
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as response:
                    assert response.status == 204 and response.read() == b""
                return url
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise RuntimeError("Synthetic container did not become ready")

    try:
        dc("create")
        dc("up", "-d")
        url = target()
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        try:
            browser.open(url + "/")
            raise AssertionError("Inventory answered with the API unreachable")
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert api_secret not in error.read().decode()

        def status(host: str) -> int:
            request = urllib.request.Request(url + "/health", headers={"Host": host})
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        assert status(OFFICE) == 204 and status("192.0.2.11") == 400
        # The publish address comes from DRIVES_BIND; unset, compose keeps loopback.
        for bind, host_ip in (("", "127.0.0.1"), (OFFICE, OFFICE)):
            config = command(*compose, "config", "--format", "json", env={**env, **bind_env(bind)})
            assert json.loads(config)["services"]["app"]["ports"][0]["host_ip"] == host_ip
        print("PASS DRIVES_BIND reaches the published binding; the configured host is answered")
        print("PASS health 204 and a read says Unavailable (503) with the API unreachable")
        for path in ("/import", "/refresh-projects", "/restore", "/deploy"):
            try:
                browser.open(url + path)
                raise AssertionError("Administrative route exposed")
            except urllib.error.HTTPError as error:
                assert error.code == 404
        try:
            browser.open(
                urllib.request.Request(
                    url + "/new",
                    data=b"label=Synthetic-rejected",
                    headers={"Origin": "http://example.invalid"},
                )
            )
            raise AssertionError("Cross-origin mutation accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        for output in (
            dc("logs", "--no-color", "app"),
            command("docker", "image", "inspect", image),
        ):
            assert api_secret not in output
        print("PASS no admin route, CSRF origin check, API secret absent from logs and image")
    finally:
        # Destructive cleanup is scoped exclusively to this generated synthetic project.
        dc("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/container_smoke.py <immutable-image>")
    smoke(sys.argv[1])
