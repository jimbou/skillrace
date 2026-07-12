import csv
import json
import sys


source, destination = sys.argv[1:]
with open(source, encoding="utf-8") as stream:
    objects = json.load(stream)
keys = sorted({key for item in objects for key in item})
with open(destination, "w", newline="", encoding="utf-8") as stream:
    if keys:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for item in objects:
            writer.writerow({key: "" if item.get(key) is None else item.get(key, "") for key in keys})
