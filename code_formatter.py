import json
import sys

code = sys.stdin.read()
formatted = json.dumps(code)
print(f'"code": {formatted}')