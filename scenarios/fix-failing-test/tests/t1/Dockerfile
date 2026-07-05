FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN cat > mathutil.py <<'IMPL'
def add(a, b):
    return a - b

def is_even(n):
    return n % 2 == 1

IMPL
RUN cat > test_mathutil.py <<'TST'
from mathutil import add, is_even

def test_add():
    assert add(2, 3) == 5

def test_even():
    assert is_even(4) is True
    assert is_even(3) is False

TST
