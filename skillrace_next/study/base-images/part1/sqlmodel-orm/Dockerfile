FROM python:3.12.13-bookworm AS checker-python
RUN python -m pip install --no-cache-dir pytest==9.1.1 fastapi==0.115.12 pydantic==2.11.4 SQLAlchemy==2.0.40 sqlmodel==0.0.24 httpx==0.28.1

FROM skillrace/pi-runtime:0.73.1
COPY --from=checker-python /usr/local /usr/local
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
