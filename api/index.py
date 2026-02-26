import os
import base64
import json
import time
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Vercel filesystem is READ-ONLY except /tmp — never write elsewhere
UPLOAD_FOLDER = "/tmp/rtrd_uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app = Flask(__name__)
CORS(app)

FAL_KEY = os.environ.get("FAL_API_KEY", "")


def ensure_tmp():
    """Create /tmp upload dir lazily inside request context (not at module level)."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def image_to_base64(path):
    with open(path, "rb") as f:
        data = f.read()
    ext = path.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def call_fal_flux(image_path: str, style_prompt: str) -> dict:
    """Submit to fal.ai and poll — total budget ~55s to stay inside Vercel 60s limit."""
    if not FAL_KEY:
        return {"error": "FAL_API_KEY environment variable is not set"}

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
    }

    b64 = image_to_base64(image_path)

    payload = {
        "prompt": style_prompt,
        "image_url": b64,
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "jpeg",
    }

    # ── Submit ────────────────────────────────────────────────
    try:
        resp = requests.post(
            "https://queue.fal.run/fal-ai/flux-kontext-pro",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return {"error": "fal.ai submit timed out"}
    except requests.exceptions.RequestException as e:
        return {"error": f"fal.ai submit error: {str(e)}"}

    if resp.status_code not in (200, 201):
        return {"error": f"fal.ai submit failed [{resp.status_code}]: {resp.text[:300]}"}

    result = resp.json()
    request_id = result.get("request_id")

    # Synchronous / immediate response (no queue)
    if not request_id:
        return parse_fal_result(result)

    # ── Poll — max 50 attempts x 1s = 50s ────────────────────
    status_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}/status"
    result_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}"

    for _ in range(50):
        time.sleep(1)
        try:
            sr = requests.get(status_url, headers=headers, timeout=10)
        except requests.exceptions.RequestException:
            continue

        if sr.status_code != 200:
            continue

        status_data = sr.json()
        status = status_data.get("status", "")

        if status == "COMPLETED":
            try:
                rr = requests.get(result_url, headers=headers, timeout=15)
                return parse_fal_result(rr.json())
            except requests.exceptions.RequestException as e:
                return {"error": f"Failed to fetch result: {str(e)}"}

        if status == "FAILED":
            reason = status_data.get("error", "unknown reason")
            return {"error": f"fal.ai job failed: {reason}"}

    return {"error": "Timed out waiting for fal.ai (>50s). Try again."}


def parse_fal_result(data: dict) -> dict:
    """Extract image URL from fal.ai response envelope."""
    images = (
        data.get("images")
        or data.get("output", {}).get("images", [])
        or []
    )
    if images:
        img = images[0]
        url = img.get("url") if isinstance(img, dict) else img
        if url:
            return {"image_url": url}
    return {"error": f"No image in fal.ai response: {json.dumps(data)[:400]}"}


def build_meme_prompt(user_note: str = "") -> str:
    base = (
        "Transform this image into an absurd, chaotic, low-IQ internet meme. "
        "Add exaggerated facial expressions with bulging eyes and shocked expressions. "
        "Add chaotic ALL-CAPS Impact font captions at top and bottom with ironic Gen-Z "
        "brainrot humor like 'no cap fr fr', 'based', 'L ratio', 'he ate', 'rizz'. "
        "Oversaturate all colors to neon extremes. Add deep-fried JPEG compression artifacts. "
        "Draw random red circles and red arrows pointing at irrelevant things. "
        "Scatter emojis across the image. "
        "Slightly bad cropping and rotation. "
        "Add 'RETARD COIN $RTRD' watermark in Comic Sans font at the bottom. "
        "The result should look made in MS Paint by a caffeinated raccoon at 3am."
    )
    if user_note:
        base += f" Additional: {user_note}"
    return base


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/meme_generator")
def meme_generator():
    return send_from_directory(".", "meme_generator.html")


@app.route("/api/generate", methods=["POST"])
def generate_meme():
    ensure_tmp()  # lazy — only when a request actually comes in

    if "image" not in request.files:
        return jsonify({"error": "No image field in request"}), 400

    file = request.files["image"]
    user_note = request.form.get("note", "")

    if not file or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Unsupported file type. Use PNG, JPG or WEBP."}), 400

    # Save to /tmp — the only writable path on Vercel
    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    upload_path = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(upload_path)
    except Exception as e:
        return jsonify({"error": f"Failed to save upload: {str(e)}"}), 500

    prompt = build_meme_prompt(user_note)
    result = call_fal_flux(upload_path, prompt)

    # Always clean up /tmp
    try:
        os.remove(upload_path)
    except Exception:
        pass

    if "error" in result:
        return jsonify(result), 500

    return jsonify({"image_url": result["image_url"], "prompt": prompt})


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "fal_key_set": bool(FAL_KEY),
        "model": "fal-ai/flux-kontext-pro",
        "upload_dir": UPLOAD_FOLDER,
    })


# Vercel calls the `app` object directly.
# Keep __main__ so local `python app.py` still works.
if __name__ == "__main__":
    print("🧠 RETARD COIN Meme Generator -> http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
