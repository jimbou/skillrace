import json
import re
import sys


template_path, data_path, output_path = sys.argv[1:]
template = open(template_path, encoding="utf-8").read()
data = json.load(open(data_path, encoding="utf-8"))
rendered = re.sub(r"\{\{([A-Za-z0-9_]+)\}\}", lambda match: str(data[match.group(1)]) if match.group(1) in data else match.group(0), template)
open(output_path, "w", encoding="utf-8").write(rendered)
