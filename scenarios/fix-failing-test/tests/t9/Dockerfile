FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN cat > loops.py <<'IMPL'
def count_up_to(n):
    out = []
    for i in range(1, n):
        out.append(i)
    return out

IMPL
RUN cat > test_loops.py <<'TST'
from loops import count_up_to

def test_count():
    assert count_up_to(3) == [1, 2, 3]

TST
