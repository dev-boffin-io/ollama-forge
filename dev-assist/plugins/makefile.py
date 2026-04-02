"""
Plugin: makefile
Trigger: "makefile" or "generate makefile"
"""

import os

TEMPLATE = """\
.PHONY: run install test clean audit tunnel

run:
\tpython main.py

install:
\tpip install -r requirements.txt

test:
\tpython -m pytest tests/ -v

clean:
\tfind . -type f -name "*.pyc" -delete
\tfind . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true

audit:
\tpython main.py audit

tunnel:
\tpython main.py tunnel 3000

lint:
\tflake8 . --max-line-length=100

format:
\tblack . --line-length=100
"""

def run(text: str = ""):
    output = "Makefile"
    if os.path.exists(output):
        confirm = input(f"  Makefile already exists. Overwrite? [y/N]: ").strip().lower()
        if confirm != "y":
            return

    with open(output, "w") as f:
        f.write(TEMPLATE)

    print(f"✅ Generated Makefile with targets:")
    for line in TEMPLATE.splitlines():
        if line.endswith(":") and not line.startswith("\t"):
            print(f"   make {line.rstrip(':')}")
