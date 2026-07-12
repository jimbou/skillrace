import re
pattern = re.compile('(?:0|[1-9][0-9]{0,2})(?:\\.(?:0|[1-9][0-9]{0,2})){3}')
def is_valid(s):
    if pattern.fullmatch(s) is None: return False
    return all(int(part) <= 255 for part in s.split('.'))
