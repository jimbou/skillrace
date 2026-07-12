import re
pattern = re.compile('[A-Za-z_][A-Za-z0-9_]*')
def is_valid(s):
    return pattern.fullmatch(s) is not None
