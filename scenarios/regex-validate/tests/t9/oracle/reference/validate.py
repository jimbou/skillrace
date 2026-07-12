import re
pattern = re.compile('(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)')
def is_valid(s):
    return pattern.fullmatch(s) is not None
