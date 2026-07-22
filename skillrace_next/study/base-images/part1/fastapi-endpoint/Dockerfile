FROM python:3.12.13-bookworm AS checker-python
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --timeout 600 pytest==9.1.1 fastapi==0.115.12 uvicorn==0.34.2 pydantic==2.11.4 SQLAlchemy==2.0.40 sqlmodel==0.0.24 httpx==0.28.1

FROM skillrace/pi-runtime:0.73.1
COPY --from=checker-python /usr/local /usr/local
WORKDIR /workspace
