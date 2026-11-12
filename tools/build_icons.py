"""Rebuild static/vendor/icons.svg from the icons the app actually uses.

Icons are rendered server-side as <svg><use href="icons.svg#name"/></svg> (see icon_svg in app.py), so there is no
icon JavaScript. Run this after you use a new icon name anywhere in templates or Python:

    npm i --prefix /tmp/lucide lucide-static@0.453.0
    python tools/build_icons.py /tmp/lucide/node_modules/lucide-static/icons

Lucide is ISC licensed (static/vendor/LUCIDE-LICENSE.txt).
"""
import os, re, sys

ALIASES = {"unlock-keyhole": "lock-keyhole-open"}          # names used here -> file name in lucide
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lucide/node_modules/lucide-static/icons"
available = {f[:-4] for f in os.listdir(src) if f.endswith(".svg")}

used = set()
for base, _, files in os.walk(ROOT):
    if any(p in base for p in ("static/vendor", "tests", ".git", "__pycache__", "node_modules", "instance")):
        continue
    for f in files:
        if f.endswith((".html", ".py", ".js")):
            text = open(os.path.join(base, f), encoding="utf8", errors="ignore").read()
            for m in re.findall(r"""['"]([a-z][a-z0-9]*(?:-[a-z0-9]+)*)['"]""", text):
                if m in available or m in ALIASES:
                    used.add(m)

out = ['<svg xmlns="http://www.w3.org/2000/svg" style="display:none">']
for name in sorted(used):
    svg = open(os.path.join(src, ALIASES.get(name, name) + ".svg")).read()
    inner = re.sub(r"\s*\n\s*", "", re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)).strip()
    out.append(f'<symbol id="{name}" viewBox="0 0 24 24">{inner}</symbol>')
out.append("</svg>")
dest = os.path.join(ROOT, "static", "vendor", "icons.svg")
open(dest, "w").write("\n".join(out))
print(f"{len(used)} icons -> {dest} ({os.path.getsize(dest)} bytes)")
