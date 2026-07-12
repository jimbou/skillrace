import re
pattern = re.compile('\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}')
def is_valid(s):
    return pattern.fullmatch(s) is not None
