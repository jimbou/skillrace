import re
def is_valid(s):
    return re.search(r'[A-Za-z0-9]+', s) is not None
