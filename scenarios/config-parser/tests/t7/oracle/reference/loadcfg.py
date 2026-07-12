import configparser
import pathlib
import sys


def fail(problem):
    print(f"error: {problem}", file=sys.stderr)
    raise SystemExit(2)


if len(sys.argv) != 2:
    fail("path is required")
config = configparser.ConfigParser()
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        config.read_file(stream)
except (OSError, configparser.Error):
    fail("config file")
if "server" not in config:
    fail("server section")
server = config["server"]
host = server.get("host", "").strip()
if not host:
    fail("host")
raw_port = server.get("port", "").strip()
try:
    port = int(raw_port)
except ValueError:
    fail("port")
if port <= 0:
    fail("port")
print(f"host={host} port={port}")
