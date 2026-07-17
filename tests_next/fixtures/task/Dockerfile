FROM skillrace/pi-base:0.73.1-deepseek-v3.2

RUN mkdir -p /home/node/.pi/agent \
    && cp /root/.pi/agent/models.json /home/node/.pi/agent/models.json \
    && chown -R node:node /home/node/.pi

WORKDIR /workspace
