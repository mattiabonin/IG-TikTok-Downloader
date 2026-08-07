"""
IG/TikTok Media Extractor - server minimale per Comandi Rapidi (Shortcuts)

Espone GET /extract?url=<link IG o TikTok>
Ritorna JSON unificato:
{
  "platform": "instagram" | "tiktok",
  "type": "video" | "image" | "carousel",
  "media": [
    {"type": "video", "url": "..."},
    {"type": "image", "url": "..."}
  ]
}

Shortcuts scarica poi ogni "url" della lista con "Get Contents of URL"
e la salva con "Save to Photo Album".
"""

from flask import Flask, request, jsonify
import yt_dlp
import requests
import os
import base64
import tempfile

app = Flask(__name__)

TIKWM_ENDPOINT = "https://www.tikwm.com/api/"

# Instagram spesso rifiuta di servire i formati video a richieste anonime
# provenienti da IP di datacenter (come quelli di Render). La soluzione è
# passare a yt-dlp i cookie di una sessione Instagram autenticata.
#
# Su Render, imposta una variabile d'ambiente IG_COOKIES_B64 con il contenuto
# di un file cookies.txt (formato Netscape) codificato in base64. Vedi GUIDA.md
# per come esportarlo dal browser.
COOKIES_FILE_PATH = None
_cookies_b64 = os.environ.get("IG_COOKIES_B64")
if _cookies_b64:
    try:
        cookies_content = base64.b64decode(_cookies_b64).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        tmp.write(cookies_content)
        tmp.close()
        COOKIES_FILE_PATH = tmp.name
    except Exception as e:
        print(f"Impossibile decodificare IG_COOKIES_B64: {e}")


def extract_tiktok(url: str) -> dict:
    r = requests.get(TIKWM_ENDPOINT, params={"url": url}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}

    media = []
    images = data.get("images") or []
    if images:
        for img in images:
            media.append({"type": "image", "url": img})
        post_type = "carousel" if len(media) > 1 else "image"
    elif data.get("play"):
        media.append({"type": "video", "url": data["play"]})
        post_type = "video"
    else:
        raise ValueError(f"Nessun media trovato: {payload.get('msg', 'errore sconosciuto')}")

    return {"platform": "tiktok", "type": post_type, "media": media}


def extract_instagram(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if COOKIES_FILE_PATH:
        ydl_opts["cookiefile"] = COOKIES_FILE_PATH
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Un post con più elementi (carosello) arriva come playlist di "entries"
    entries = info.get("entries") if info.get("_type") == "playlist" else [info]

    media = []
    for entry in entries:
        if not entry:
            continue
        is_video = entry.get("ext") in ("mp4", "mov") or (
            entry.get("vcodec") and entry.get("vcodec") != "none"
        )
        direct_url = entry.get("url")
        if not direct_url and entry.get("formats"):
            direct_url = entry["formats"][-1].get("url")
        if not direct_url:
            continue
        media.append({"type": "video" if is_video else "image", "url": direct_url})

    if not media:
        raise ValueError("Nessun media estraibile dal post (privato o URL non valido)")

    post_type = "carousel" if len(media) > 1 else media[0]["type"]
    return {"platform": "instagram", "type": post_type, "media": media}


class _LogCapture:
    def __init__(self):
        self.lines = []

    def debug(self, msg):
        self.lines.append(f"DEBUG: {msg}")

    def info(self, msg):
        self.lines.append(f"INFO: {msg}")

    def warning(self, msg):
        self.lines.append(f"WARNING: {msg}")

    def error(self, msg):
        self.lines.append(f"ERROR: {msg}")


@app.route("/debug-extract")
def debug_extract():
    """Endpoint temporaneo: prova un'estrazione Instagram catturando i log interni di yt-dlp."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "parametro 'url' mancante, es. ?url=https://www.instagram.com/p/XXX/"}), 400

    logcap = _LogCapture()
    ydl_opts = {
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "verbose": True,
        "logger": logcap,
    }
    if COOKIES_FILE_PATH:
        ydl_opts["cookiefile"] = COOKIES_FILE_PATH

    result = {"cookies_used": COOKIES_FILE_PATH is not None}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        result["success"] = True
        result["keys_found"] = list(info.keys()) if info else []
    except Exception as e:
        result["success"] = False
        result["exception"] = str(e)
    result["log"] = logcap.lines[-40:]  # ultime 40 righe, per non appesantire troppo
    return jsonify(result)


@app.route("/extract")
def extract():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "parametro 'url' mancante"}), 400

    try:
        if "tiktok.com" in url:
            result = extract_tiktok(url)
        elif "instagram.com" in url:
            result = extract_instagram(url)
        else:
            return jsonify({"error": "piattaforma non supportata (solo Instagram/TikTok)"}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "cookies_loaded": COOKIES_FILE_PATH is not None,
    })


@app.route("/debug-cookies")
def debug_cookies():
    """Endpoint temporaneo per diagnosticare problemi con i cookie Instagram."""
    if not COOKIES_FILE_PATH:
        return jsonify({"cookies_loaded": False, "reason": "IG_COOKIES_B64 non impostata o decodifica fallita"})
    try:
        with open(COOKIES_FILE_PATH, "r") as f:
            content = f.read()
        lines = content.splitlines()
        non_comment_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        domains = set()
        for l in non_comment_lines:
            parts = l.split("\t")
            if parts:
                domains.add(parts[0])
        return jsonify({
            "cookies_loaded": True,
            "total_lines": len(lines),
            "cookie_entries": len(non_comment_lines),
            "domains_found": list(domains)[:10],
            "starts_with_netscape_header": content.startswith("# Netscape") or content.startswith("# HTTP Cookie"),
        })
    except Exception as e:
        return jsonify({"cookies_loaded": True, "error_reading": str(e)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
