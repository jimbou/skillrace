import sys


counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
with open(sys.argv[1], encoding="utf-8") as stream:
    for line in stream:
        fields = line.split(maxsplit=2)
        if len(fields) == 3 and fields[1] in counts:
            counts[fields[1]] += 1
for level in ("INFO", "WARN", "ERROR"):
    print(f"{level}={counts[level]}")
