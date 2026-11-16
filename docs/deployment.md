# Deployment

Synthetic, loopback-only. The app owns no records and no database; it is a client of the t6 API.

## Settings (runtime only)

| Name | Meaning |
| --- | --- |
| `T6_API_URL` | Address of the t6 API. |
| `T6_API_SECRET` | The t6 API secret bound to the custodian's own Person. The API authenticates it; the app sends no actor. |
| `DRIVES_SESSION_SECRET` | At least 32 characters; signs form tokens. Rotating it invalidates open forms. |
| `DRIVES_OUTBOX` | The outbox file, directly inside `/data` (default `/data/outbox.json`). |

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

The smoke starts the image with an unreachable API address and checks loopback binding, non-root user, read-only root, dropped capabilities, `/health` 204, a read answering 503 Unavailable, no admin route, the CSRF origin check, and that the secret is not in the logs or image.

## Start

```sh
export DRIVES_IMAGE="$(cat "$TMPDIR/drives-image.id")"
export DRIVES_SESSION_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export T6_API_URL=... T6_API_SECRET=...
docker compose -p drives up -d
curl --fail -i http://127.0.0.1:5000/health
```

`/health` answers 204 for process readiness only and never calls the API. The published port stays on host loopback; only exact `localhost` / `127.0.0.1` authorities are accepted.

## When the API is down

- Every read screen answers 503 "Unavailable".
- A write that cannot reach the API (no connection, timeout, or a 5xx answer) is queued in the outbox file and the page says so. It is sent again with the same idempotency key, at the start of a request at most every 5 seconds, so it is recorded once.
- A queued write the API later refuses shows as Failed, with the reason, on `/outbox`. The custodian dismisses it there.
- The outbox is the only file in `/data`. Keep the volume while writes are queued.

The workbook import runs with the `t6` command against the t6 database, not here.
