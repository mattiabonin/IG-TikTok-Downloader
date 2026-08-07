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

app = Flask(__name__)

TIKWM_ENDPOINT = "https://www.tikwm.com/api/"


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
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
