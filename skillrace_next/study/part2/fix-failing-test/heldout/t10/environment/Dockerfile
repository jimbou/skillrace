FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN cat > safe.py <<'IMPL'
def safe_div(a, b):
    return a / b

IMPL
RUN cat > test_safe.py <<'TST'
from safe import safe_div

def test_div():
    assert safe_div(6, 3) == 2

def test_zero():
    assert safe_div(6, 0) is None

TST
