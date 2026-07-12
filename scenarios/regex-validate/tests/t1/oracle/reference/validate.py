import re
pattern = re.compile('(?:[01][0-9]|2[0-3]):[0-5][0-9]')
def is_valid(s):
    return pattern.fullmatch(s) is not None
