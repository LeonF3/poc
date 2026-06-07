"""
Combines upload.html, case_files.html, intel_map.html, about.html
into a single combined.html where tabs switch via JS show/hide.
Injects live RSA data if provided.
"""
import re
import os
import json

APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_FILES = [
    ("upload",     "upload.html"),
    ("case_files", "case_files.html"),
    ("intel_map",  "intel_map.html"),
    ("about",      "about.html"),
]

TAB_IDS = [h[0] for h in HTML_FILES]


def _extract_body(html: str) -> str:
    """Extract content between <body> tags (or full html if no body tag)."""
    m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else html


def _extract_head_extras(html: str) -> str:
    """Extract <style> and <script> blocks from <head>."""
    head_m = re.search(r"<head[^>]*>(.*?)</head>", html, re.DOTALL | re.IGNORECASE)
    if not head_m:
        return ""
    head = head_m.group(1)
    # Remove <title> and font/meta links — keep styles and scripts
    head = re.sub(r"<title[^>]*>.*?</title>", "", head, flags=re.DOTALL | re.IGNORECASE)
    head = re.sub(r'<link[^>]*fonts\.googleapis[^>]*>', "", head, flags=re.IGNORECASE)
    head = re.sub(r'<link[^>]*fonts\.gstatic[^>]*>', "", head, flags=re.IGNORECASE)
    head = re.sub(r'<meta[^>]*>', "", head, flags=re.IGNORECASE)
    return head.strip()


def build_combined(rsa_data: dict | None = None, video_url: str = "") -> str:
    """Build combined single-page HTML with JS tab switching."""

    # Read first file for shared head (fonts, CSS, shared scripts)
    first_path = os.path.join(APP_DIR, HTML_FILES[0][1])
    with open(first_path, encoding="utf-8") as f:
        first_html = f.read()

    shared_head = _extract_head_extras(first_html)
    # Patch video URL in shared head if needed
    if video_url:
        shared_head = re.sub(
            r'(<video\s[^>]*?)src=["\'][^"\']*["\']',
            r'\1src="' + video_url + '"',
            shared_head
        )

    # Collect body content per page
    page_bodies = {}
    for tab_id, fname in HTML_FILES:
        path = os.path.join(APP_DIR, fname)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        body = _extract_body(html)
        # Patch video in body
        if video_url:
            body = re.sub(
                r'(<video\s[^>]*?)src=["\'][^"\']*["\']',
                r'\1src="' + video_url + '"',
                body
            )
        # Remove duplicate nav/statusbar — they'll be rendered once
        # (they're injected by JS so body content is fine as-is)
        page_bodies[tab_id] = body

    # Build the combined HTML
    pages_html = ""
    for tab_id, _ in HTML_FILES:
        display = "block" if tab_id == "upload" else "none"
        pages_html += (
            f'<div id="page-{tab_id}" class="rsa-page" '
            f'style="display:{display};">\n'
            f'{page_bodies[tab_id]}\n'
            f'</div>\n'
        )

    # Data injection script
    data_script = ""
    if rsa_data:
        data_script = f"""<script>
/* Live RSA data injected by build_combined.py */
(function(g){{
  var d = {json.dumps(rsa_data, ensure_ascii=False)};
  function level(score){{
    if(score>=90) return {{key:"CRITICAL",label:"CRITICAL",color:"var(--red)",   hex:"#EF4444",glyph:"☠"}};
    if(score>=70) return {{key:"THREAT",  label:"THREAT",  color:"var(--orange)",hex:"#F97316",glyph:"✖"}};
    if(score>=45) return {{key:"CAUTION", label:"CAUTION", color:"var(--gold)",  hex:"#F59E0B",glyph:"▲"}};
    return              {{key:"MONITOR", label:"MONITOR", color:"var(--green)", hex:"#22C55E",glyph:"●"}};
  }}
  function mission(score){{
    if(score>=70) return {{key:"URGENT", cls:"urgent", glyph:"⚡",text:"IMMEDIATE ACTION",color:"var(--red)",   hex:"#EF4444",bg:"#2D0A0A"}};
    if(score>=45) return {{key:"REVIEW", cls:"review", glyph:"\U0001f50d",text:"INVESTIGATE",  color:"var(--orange)",hex:"#F97316",bg:"#2D1A0A"}};
    return               {{key:"MONITOR",cls:"monitor",glyph:"\U0001f441",text:"WATCH LIST",   color:"var(--green)", hex:"#22C55E",bg:"#0A2D0A"}};
  }}
  function signalOf(key,sigs){{for(var i=0;i<sigs.length;i++)if(sigs[i].key===key)return sigs[i];return{{short:key,full:key,key:key}};}}
  var SIGNALS = d.SIGNAL_COUNTS.map(function(sc){{return sc.signal;}});
  var CASES = d.CASES;
  CASES.sort(function(a,b){{return b.score-a.score;}});
  CASES.forEach(function(c,i){{
    c.rank=i+1; c.id="#"+String(i+1).padStart(3,"0");
    c.level=c.level||level(c.score);
    c.mission=c.mission||mission(c.score);
    c.sig=c.sig||signalOf(c.signal,SIGNALS);
  }});
  g.RSA = {{
    SIGNALS:SIGNALS, CASES:CASES,
    SIGNAL_COUNTS:d.SIGNAL_COUNTS,
    STATS:d.STATS,
    level:level, mission:mission, signalOf:function(k){{return signalOf(k,SIGNALS);}},
    analyst:d.analyst||"LEON FRANKLIN"
  }};
}})(window);
</script>"""

    tab_switcher = """<script>
/* Tab switcher — intercepts nav <a> clicks, shows/hides pages */
(function() {
  var TAB_MAP = {
    "upload.html":     "upload",
    "case_files.html": "case_files",
    "intel_map.html":  "intel_map",
    "about.html":      "about"
  };

  function showTab(tabId) {
    document.querySelectorAll(".rsa-page").forEach(function(el) {
      el.style.display = "none";
    });
    var page = document.getElementById("page-" + tabId);
    if (page) page.style.display = "block";

    // Re-render nav with correct active state
    if (window.RSA && window.RSA.ui && window.RSA.ui.renderNav) {
      var existing = document.querySelector("header.nav");
      if (existing) existing.remove();
      var tmpDiv = document.createElement("div");
      tmpDiv.innerHTML = window.RSA.ui.renderNav(tabId === "case_files" ? "cases"
                                               : tabId === "intel_map" ? "intel"
                                               : tabId);
      var newNav = tmpDiv.firstChild;
      document.querySelector(".app") && document.querySelector(".app").prepend(newNav);
    }

    // Fix video in newly shown page
    fixVideo();
  }

  function fixVideo() {
    document.querySelectorAll("video").forEach(function(v) {
      if (!v.src || v.src.indexOf("blob:") === 0) return;
      var src = v.getAttribute("src") || "";
      if (src && src.indexOf("://") === -1) {
        v.src = window.location.origin + (src.startsWith("/") ? "" : "/") + src;
      }
      v.load();
      v.play().catch(function(){});
    });
  }

  document.addEventListener("click", function(e) {
    var a = e.target.closest("a[href]");
    if (!a) return;
    var href = a.getAttribute("href") || "";
    var fname = href.split("/").pop().split("?")[0];
    if (TAB_MAP[fname]) {
      e.preventDefault();
      showTab(TAB_MAP[fname]);
    }
  });

  // Fix video on load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fixVideo);
  } else {
    fixVideo();
  }
  setTimeout(fixVideo, 800);
})();
</script>"""

    combined = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Risk Signal Aggregator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@400;500;600;700;900&family=Saira:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
{data_script}
{shared_head}
{tab_switcher}
</head>
<body>
<div class="app">
{pages_html}
</div>
</body>
</html>"""

    return combined


def write_combined(output_path: str, rsa_data: dict | None = None, video_url: str = ""):
    html = build_combined(rsa_data, video_url)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path
