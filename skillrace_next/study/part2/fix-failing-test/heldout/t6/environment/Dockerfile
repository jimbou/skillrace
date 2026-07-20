FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN cat > lookup.py <<'IMPL'
def price_of(catalog, name):
    return catalog[name.upper()]

IMPL
RUN cat > test_lookup.py <<'TST'
from lookup import price_of

def test_price():
    assert price_of({'apple': 3}, 'apple') == 3

TST
