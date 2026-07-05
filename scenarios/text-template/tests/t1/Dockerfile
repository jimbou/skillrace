FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN printf 'Hi {{name}}, {{count}} msgs. Keep {x} literal.' > tmpl.txt
RUN printf '{"name":"Sam","count":"3"}' > data.json
