import re, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HTML_FILES = ["upload.html", "case_files.html", "intel_map.html", "about.html"]
PORT       = 8507
VIDEO_URL  = f"http://localhost:{PORT}/app/static/background.mp4"
PAGES_BASE = f"http://localhost:{PORT}/app/static/pages"
APP_DIR    = os.path.dirname(os.path.abspath(__file__))
PAGES_DIR  = os.path.join(APP_DIR, "static", "pages")
os.makedirs(PAGES_DIR, exist_ok=True)

for fname in HTML_FILES:
    with open(os.path.join(APP_DIR, fname), encoding="utf-8") as f:
        html = f.read()

    # Patch <video src="threat.mp4">
    html = re.sub(
        r'(<video\s[^>]*?)src=["\'][^"\']*["\']',
        r'\1src="' + VIDEO_URL + '"',
        html
    )

    # Patch TABS JS array: href:"upload.html" → href:"http://..."
    for fn in HTML_FILES:
        html = html.replace(f'href:"{fn}"', f'href:"{PAGES_BASE}/{fn}"')

    out = os.path.join(PAGES_DIR, fname)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    nav_ok = PAGES_BASE in html
    vid_ok = VIDEO_URL in html or fname != "upload.html"
    print(f"{fname}: nav={nav_ok}  video={vid_ok}")

print("Done — pages written to app/static/pages/")
