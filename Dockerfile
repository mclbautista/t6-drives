# Both stages use the same reviewed multi-platform release digest.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.lock ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DRIVES_DB=/data/drives.sqlite
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels \
    && groupadd --gid 10001 drives && useradd --uid 10001 --gid 10001 --no-create-home drives \
    && mkdir /data && chown 10001:10001 /data && chmod 700 /data
WORKDIR /app
COPY app.py assignments.py custody.py shipments.py import_store.py imports.py imported_shipments.py runtime.py ./
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2); assert r.status == 204 and r.read() == b''"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--worker-tmp-dir", "/tmp", "--error-logfile", "-", "runtime:application()"]
