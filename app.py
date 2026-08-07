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
import instaloader
import requests
import os
import base64
import tempfile
import re
from http.cookiejar import MozillaCookieJar

app = Flask(__name__)

TIKWM_ENDPOINT = "https://www.tikwm.com/api/"

# Instagram spesso rifiuta di servire i media a richieste anonime provenienti
# da IP di datacenter (come quelli di Render). La soluzione è passare a
# instaloader i cookie di una sessione Instagram autenticata.
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

SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#&]+)")


def _build_instaloader_context() -> instaloader.Instaloader:
    """Crea un contesto instaloader, autenticato con i cookie se disponibili."""
    L = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )
    if COOKIES_FILE_PATH:
        cj = MozillaCookieJar(COOKIES_FILE_PATH)
        cj.load(ignore_discard=True, ignore_expires=True)
        L.context._session.cookies.update(cj)
        csrf_token = None
        username = None
        for cookie in cj:
            if cookie.name == "csrftoken":
                csrf_token = cookie.value
            if cookie.name == "ds_user_id":
                username = cookie.value
        if csrf_token:
            L.context._session.headers.update({"X-CSRFToken": csrf_token})
        if username:
            L.context.username = username
    return L


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
    match = SHORTCODE_RE.search(url)
    if not match:
        raise ValueError("URL Instagram non riconosciuto (atteso un link a /p/, /reel/ o /tv/)")
    shortcode = match.group(1)

    L = _build_instaloader_context()
    post = instaloader.Post.from_shortcode(L.context, shortcode)

    media = []
    if post.typename == "GraphSidecar":
        for node in post.get_sidecar_nodes():
            if node.is_video:
                media.append({"type": "video", "url": node.video_url})
            else:
                media.append({"type": "image", "url": node.display_url})
    elif post.is_video:
        media.append({"type": "video", "url": post.video_url})
    else:
        media.append({"type": "image", "url": post.url})

    if not media:
        raise ValueError("Nessun media estraibile dal post (privato, rimosso o URL non valido)")

    post_type = "carousel" if len(media) > 1 else media[0]["type"]
    return {"platform": "instagram", "type": post_type, "media": media}


@app.route("/debug-extract")
def debug_extract():
    """Endpoint temporaneo: prova un'estrazione Instagram e mostra diagnostica dettagliata."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "parametro 'url' mancante, es. ?url=https://www.instagram.com/p/XXX/"}), 400

    result = {"cookies_used": COOKIES_FILE_PATH is not None}
    try:
        match = SHORTCODE_RE.search(url)
        if not match:
            result["success"] = False
            result["exception"] = "shortcode non trovato nell'URL"
            return jsonify(result)
        shortcode = match.group(1)
        result["shortcode"] = shortcode

        L = _build_instaloader_context()
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        result["typename"] = post.typename
        result["is_video"] = post.is_video
        if post.typename == "GraphSidecar":
            nodes = list(post.get_sidecar_nodes())
            result["sidecar_count"] = len(nodes)
            result["sidecar_types"] = ["video" if n.is_video else "image" for n in nodes]
        result["success"] = True
    except Exception as e:
        result["success"] = False
        result["exception"] = f"{type(e).__name__}: {e}"
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
