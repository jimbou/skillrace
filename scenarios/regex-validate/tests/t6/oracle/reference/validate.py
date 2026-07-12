import re
pattern = re.compile('\\#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})')
def is_valid(s):
    return pattern.fullmatch(s) is not None
