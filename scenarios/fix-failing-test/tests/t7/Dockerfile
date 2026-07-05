FROM skillrace/skillgen-base:latest
WORKDIR /workspace
RUN cat > strutil.py <<'IMPL'
def initials(name):
    return ''.join(p[0] for p in name.split('-'))

IMPL
RUN cat > test_strutil.py <<'TST'
from strutil import initials

def test_initials():
    assert initials('John Ronald Reuel') == 'JRR'

TST
