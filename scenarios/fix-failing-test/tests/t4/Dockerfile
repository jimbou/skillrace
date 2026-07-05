FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN cat > cmp.py <<'IMPL'
def at_least(x, threshold):
    return x > threshold

IMPL
RUN cat > test_cmp.py <<'TST'
from cmp import at_least

def test_at_least():
    assert at_least(5, 5) is True
    assert at_least(6, 5) is True
    assert at_least(4, 5) is False

TST
