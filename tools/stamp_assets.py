"""Add a version to every CSS/JS reference in the built site so browsers never mix a new
page with old cached files.  Usage: python tools/stamp_assets.py _site <version>"""

import re
import sys
from pathlib import Path

root, version = Path(sys.argv[1]), sys.argv[2]
index = root / "index.html"
html = index.read_text()
html = re.sub(r'(href|src)="(assets/[^"?]+\.(?:css|js|svg))"', rf'\1="\2?v={version}"', html)
index.write_text(html)
for js in (root / "assets").rglob("*.js"):
    text = js.read_text()
    stamped = re.sub(r'''(from\s+["'])(\.{1,2}/[^"'?]+\.js)(["'])''', rf"\1\2?v={version}\3", text)
    stamped = re.sub(r'''(new URL\(["'])(\.{1,2}/[^"'?]+\.json)(["'])''', rf"\1\2?v={version}\3", stamped)
    if stamped != text:
        js.write_text(stamped)
print(f"Stamped assets with v={version}")
