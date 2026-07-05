FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN cat > core.py <<'IMPL'
from .helpers import compute

def run(x):
    return compute(x) + 1

IMPL
RUN cat > test_core.py <<'TST'
from core import run

def test_run():
    assert run(3) == 7

TST
RUN cat > helpers.py <<'H'
def compute(x):
    return x * 2
H
