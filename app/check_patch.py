import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VIDEO_URL = "/app/static/background.mp4"
with open("upload.html", encoding="utf-8") as f:
    html = f.read()

m = re.search(r"<video[^>]+>", html)
print("ORIGINAL:", m.group(0) if m else "NOT FOUND")

patched = re.sub(r'(<video\s[^>]*?)src=["\'][^"\']*["\']', r'\1src="' + VIDEO_URL + '"', html)
m2 = re.search(r"<video[^>]+>", patched)
print("PATCHED: ", m2.group(0) if m2 else "NOT FOUND")
print("threat.mp4 remaining:", patched.count("threat.mp4"))
