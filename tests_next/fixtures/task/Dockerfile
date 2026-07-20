FROM python:3.12.13-bookworm AS checker-python

RUN python -m pip install --no-cache-dir pytest==9.1.1

FROM skillrace/pi-runtime:0.73.1

COPY --from=checker-python /usr/local /usr/local

RUN mkdir -p /home/node/.pi/agent \
    && cp /root/.pi/agent/models.json /home/node/.pi/agent/models.json \
    && chown -R node:node /home/node/.pi

WORKDIR /workspace
