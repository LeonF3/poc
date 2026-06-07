import re

for fname in ["upload.html", "case_files.html"]:
    with open(fname, encoding="utf-8") as f:
        html = f.read()

    hrefs = re.findall(r'href=["\'][^"\']{0,80}["\']', html)
    print(f"{fname} hrefs:", hrefs[:8])

    html_refs = re.findall(r'["\'][^"\']*\.html[^"\']*["\']', html)
    print(f"{fname} .html refs:", html_refs[:5])

    # Check nav section specifically
    nav_idx = html.find("nav")
    if nav_idx > 0:
        print(f"{fname} nav section:", html[nav_idx:nav_idx+400])
    print()
