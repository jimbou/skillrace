import re
pattern = re.compile('[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])')
def is_valid(s):
    return pattern.fullmatch(s) is not None
