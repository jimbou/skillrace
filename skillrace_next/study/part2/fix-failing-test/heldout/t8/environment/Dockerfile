FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN cat > calc.py <<'IMPL'
def double(x):
    result = x * 2

IMPL
RUN cat > test_calc.py <<'TST'
from calc import double

def test_double():
    assert double(5) == 10

TST
