FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN printf 'Hi {{name}}, {{count}} msgs. Keep {x} literal.' > tmpl.txt
RUN printf '{"name":"Sam","count":"3"}' > data.json
