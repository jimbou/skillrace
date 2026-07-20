FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN cat > seq.py <<'IMPL'
def first_n(xs, n):
    return xs[:n-1]

IMPL
RUN cat > test_seq.py <<'TST'
from seq import first_n

def test_first_n():
    assert first_n([1,2,3,4], 2) == [1,2]
    assert first_n([1,2,3], 3) == [1,2,3]

TST
