FROM python:3.12.13-bookworm AS checker-python
RUN python -m pip install --no-cache-dir pytest==9.1.1 numpy==2.2.6 pandas==2.2.3

FROM skillrace/pi-runtime:0.73.1
COPY --from=checker-python /usr/local /usr/local
WORKDIR /workspace
