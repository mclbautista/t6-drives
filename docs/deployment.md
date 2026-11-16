# Deployment

Loopback-only by default; reachable from the office network only when configured (see [Office network](#office-network)). The app owns no records and no database; it is a client of the t6 API.

## Settings (runtime only)

| Name | Meaning |
| --- | --- |
| `T6_API_URL` | Address of the t6 API. |
| `T6_API_SECRET` | The t6 API secret bound to the custodian's own Person. The API authenticates it; the app sends no actor. |
| `DRIVES_SESSION_SECRET` | At least 32 characters; signs form tokens. Rotating it invalidates open forms. |
| `DRIVES_OUTBOX` | The outbox file, directly inside `/data` (default `/data/outbox.json`). |
| `DRIVES_ALLOWED_HOST` | Optional. One office host name or IP address accepted besides `localhost` and `127.0.0.1` (see [Office network](#office-network)). |

Secrets are never in the image, a build argument, a log line or an error page. Compose takes them from the host environment and refuses to start without them.

## Build and check

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock -r requirements-dev.lock
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy
.venv/bin/python -m build
docker build --iidfile "$TMPDIR/drives-image.id" .
.venv/bin/python tools/container_smoke.py "$(cat "$TMPDIR/drives-image.id")"
```

The smoke starts the image with an unreachable API address and checks loopback binding, that `DRIVES_BIND` reaches the published binding, that the configured `DRIVES_ALLOWED_HOST` is answered and another host refused, non-root user, read-only root, dropped capabilities, `/health` 204, a read answering 503 Unavailable, no admin route, the CSRF origin check, and that the secret is not in the logs or image.

## Start

```sh
export DRIVES_IMAGE="$(cat "$TMPDIR/drives-image.id")"
export DRIVES_SESSION_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export T6_API_URL=... T6_API_SECRET=...
docker compose -p drives up -d
curl --fail -i http://127.0.0.1:5000/health
```

`/health` answers 204 for process readiness only and never calls the API. The published port stays on host loopback unless `DRIVES_BIND` says otherwise; only exact `localhost` / `127.0.0.1` authorities, plus `DRIVES_ALLOWED_HOST` if set, are accepted.

## Office network

Decision (owner, 2026-11-16, for gates G1 to G3, #17): the app runs on the studio server next to the t6 API and is reachable from the office network only. Everyone who can open it writes as the drive custodian, through the custodian's own t6 API secret. There is no per-user login.

Two settings, both optional, both empty by default (loopback):

| Name | Meaning |
| --- | --- |
| `DRIVES_BIND` | Address compose publishes the port on (default `127.0.0.1`). Use the office-network address of the host; never `0.0.0.0`. |
| `DRIVES_ALLOWED_HOST` | The one host name or IP address the app accepts in the `Host` header besides `localhost` and `127.0.0.1`. No port, scheme, list or wildcard; a value with `/`, `:`, `*`, `,`, whitespace, or `0.0.0.0` stops the app at start-up with an error naming the setting. |

```sh
export DRIVES_BIND=192.0.2.10 DRIVES_ALLOWED_HOST=192.0.2.10   # the office-network address of the host
docker compose -p drives up -d
```

The port rule and the rule that `Origin` must equal the request's own host URL are unchanged, and forwarded headers (`X-Forwarded-Host`, `Forwarded`) are never trusted.

The app speaks plain HTTP (no TLS), so it must never be published beyond the office network.

Check: from another office machine, open `http://<address>:5000/health` (204) and the inventory; both work. A request with any other `Host` answers 400:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: other.example' http://192.0.2.10:5000/health   # 400
```

## When the API is down

- Every read screen answers 503 "Unavailable".
- A write that cannot reach the API (no connection, timeout, or a 5xx answer) is queued in the outbox file and the page says so. It is sent again with the same idempotency key, at the start of a request at most every 5 seconds, so it is recorded once.
- A queued write the API later refuses shows as Failed, with the reason, on `/outbox`. The custodian dismisses it there. A custody step that cannot follow the last one, or an edit of a Drive that changed since its form was opened, is refused the same way and is never sent again.
- The outbox is the only file in `/data`. Keep the volume while writes are queued.

The workbook import runs with the `t6` command against the t6 database, not here.
