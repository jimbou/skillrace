FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN cat > acc.py <<'IMPL'
def add_item(item, bucket=[]):
    bucket.append(item)
    return bucket

IMPL
RUN cat > test_acc.py <<'TST'
from acc import add_item

def test_fresh_each_call():
    assert add_item(1) == [1]
    assert add_item(2) == [2]

TST
