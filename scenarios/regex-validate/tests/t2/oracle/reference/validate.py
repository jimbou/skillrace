import re
pattern = re.compile('(?=.{1,40}\\Z)[a-z0-9]+(?:-[a-z0-9]+)*')
def is_valid(s):
    return pattern.fullmatch(s) is not None
