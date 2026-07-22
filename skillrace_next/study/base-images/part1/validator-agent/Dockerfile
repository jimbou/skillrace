FROM python:3.12.13-bookworm AS checker-python
RUN python -m pip install --no-cache-dir pytest==9.1.1

FROM skillrace/pi-runtime:0.73.1
COPY --from=checker-python /usr/local /usr/local
RUN sed -i \
        -e 's#http://deb.debian.org/debian-security#https://mirrors.aliyun.com/debian-security#g' \
        -e 's#http://deb.debian.org/debian#https://mirrors.aliyun.com/debian#g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
