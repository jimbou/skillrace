import re
pattern = re.compile('[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\\.[A-Za-z]{2,}')
def is_valid(s):
    return pattern.fullmatch(s) is not None
