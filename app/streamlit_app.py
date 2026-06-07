import streamlit as st
import os
import sys
import shutil
import socketserver
import threading
import http.server
import socket
import json
import urllib.parse

POC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR  = os.path.dirname(os.path.abspath(__file__))
SERVE_DIR = os.path.join(APP_DIR, "static", "serve")
sys.path.insert(0, POC_ROOT)

from app.data_bridge import build_rsa_data, inject_data_into_html

st.set_page_config(
    page_title="Risk Signal Aggregator",
    page_icon="🛡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.stApp { background:#0A0A0F !important; }
[data-testid="stSidebar"] { background:#0D0D14; }
#MainMenu, footer, header { visibility:hidden; }
.block-container { padding:0 !important; max-width:100% !important; }
section[data-testid="stMain"] > div { padding:0 !important; }
iframe { border:none !important; display:block; }
</style>
""", unsafe_allow_html=True)

HTML_FILES = ["upload.html", "case_files.html", "intel_map.html", "about.html"]

SAMPLE_OUTPUT_PATH = os.path.join(POC_ROOT, "outputs", "sample_risk_output.json")


def _load_demo_rsa() -> dict:
    """Pre-build RSA payload from saved sample output (no API calls)."""
    with open(SAMPLE_OUTPUT_PATH, encoding="utf-8") as f:
        output = json.load(f)
    return build_rsa_data(output)


# Pre-load once at import time so the HTTP handler can return it instantly
_DEMO_RSA_JSON: bytes = json.dumps(_load_demo_rsa(), ensure_ascii=False).encode("utf-8")


# ── Threaded HTTP server + /analyze API endpoint ──────────────────────────────
class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@st.cache_resource
def start_file_server() -> int:
    os.makedirs(SERVE_DIR, exist_ok=True)

    port = 8600
    for p in range(8600, 8620):
        with socket.socket() as s:
            if s.connect_ex(("localhost", p)) != 0:
                port = p
                break

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=SERVE_DIR, **kwargs)

        def log_message(self, *a):
            pass

        def do_GET(self):
            super().do_GET()

        def do_POST(self):
            self.send_response(404)
            self.end_headers()

    server = _ThreadedHTTPServer(("localhost", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return port


HTML_PORT = start_file_server()
HTML_BASE = f"http://localhost:{HTML_PORT}"


# ── Copy HTML files into serve directory ───────────────────────────────────────
def refresh_serve_dir():
    os.makedirs(SERVE_DIR, exist_ok=True)

    for fname in HTML_FILES:
        src = os.path.join(APP_DIR, fname)
        if not os.path.exists(src):
            continue
        with open(src, encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(SERVE_DIR, fname), "w", encoding="utf-8") as f:
            f.write(html)

    # Write pre-built demo data as a static JSON file — fetched by JS after 2s delay
    with open(os.path.join(SERVE_DIR, "demo_data.json"), "wb") as f:
        f.write(_DEMO_RSA_JSON)

    video_src = os.path.join(APP_DIR, "threat.mp4")
    video_dst = os.path.join(SERVE_DIR, "threat.mp4")
    if os.path.exists(video_src) and not os.path.exists(video_dst):
        shutil.copy2(video_src, video_dst)


if "serve_built" not in st.session_state:
    refresh_serve_dir()
    st.session_state.serve_built = True


# ── Minimal sidebar (HTML panel is the real control center) ───────────────────
with st.sidebar:
    st.caption("Use the app panel to upload data and run analysis.")


# ── Render iframe ──────────────────────────────────────────────────────────────
params  = st.query_params
cur_tab = params.get("tab", "upload")
tab_file_map = {
    "upload":     "upload.html",
    "case_files": "case_files.html",
    "intel_map":  "intel_map.html",
    "about":      "about.html",
}
cur_file = tab_file_map.get(cur_tab, "upload.html")

st.markdown(
    f'<iframe src="{HTML_BASE}/{cur_file}" '
    f'width="100%" height="960" '
    f'style="border:none;display:block;" '
    f'allow="autoplay">'
    f'</iframe>',
    unsafe_allow_html=True,
)
