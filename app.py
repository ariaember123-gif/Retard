import os
import base64
import json
import time
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

FAL_KEY = os.environ.get("FAL_API_KEY", "")

MEME_SYSTEM_PROMPT = """You are an image transformation engine that converts normal images into absurd, chaotic, low-IQ internet meme energy.

Transform the uploaded image by:
1. Adding exaggerated facial expressions with bulging eyes, gaping mouth, shocked/dumb expressions if faces present
2. Overlaying chaotic ALL-CAPS Impact font captions (top and bottom) with ironic brainrot humor
3. Distorting text with random sizes, Comic Sans vibes, tilted angles
4. Cranking saturation to maximum — neon colors bleeding everywhere
5. Bad cropping — off-center, slightly rotated
6. Drawing random red circles and red arrows pointing at random irrelevant things
7. Scattering random emojis (🧠💀🔥😭🤣🚀💎🤪) across the image
8. Adding deep-fried JPG compression artifacts aesthetic
9. Gen-Z brainrot captions like "no cap fr fr", "based", "L + ratio", "he ate", "slay", "rizz", "bussin"
10. Optional: adding wojak, pepe, or spinning shiba inu overlay in corner

The result should look like it was made in MS Paint by a caffeinated raccoon at 3am."""


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def image_to_base64(path):
    with open(path, "rb") as f:
        data = f.read()
    ext = path.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def call_fal_flux(image_path: str, style_prompt: str) -> dict:
    """Call fal.ai flux-kontext-pro for image-to-image meme transformation."""
    if not FAL_KEY:
        return {"error": "FAL_API_KEY not set in environment"}

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

    # Submit job
    submit_url = "https://queue.fal.run/fal-ai/flux-kontext-pro"
    resp = requests.post(submit_url, headers=headers, json=payload, timeout=60)

    if resp.status_code not in (200, 201):
        return {"error": f"fal.ai submit failed: {resp.status_code} {resp.text[:300]}"}

    result = resp.json()
    request_id = result.get("request_id")
    if not request_id:
        # Synchronous response
        return parse_fal_result(result)

    # Poll for result
    status_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}/status"
    result_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}"

    for attempt in range(60):  # up to 60s
        time.sleep(1)
        sr = requests.get(status_url, headers=headers, timeout=30)
        if sr.status_code != 200:
            continue
        status = sr.json().get("status", "")
        if status == "COMPLETED":
            rr = requests.get(result_url, headers=headers, timeout=30)
            return parse_fal_result(rr.json())
        elif status == "FAILED":
            return {"error": "fal.ai job failed"}

    return {"error": "Timeout waiting for fal.ai result"}


def parse_fal_result(data: dict) -> dict:
    """Extract image URL from fal.ai response."""
    images = data.get("images") or data.get("output", {}).get("images", [])
    if images and len(images) > 0:
        img = images[0]
        url = img.get("url") if isinstance(img, dict) else img
        return {"image_url": url}
    return {"error": f"No image in response: {json.dumps(data)[:300]}"}


def build_meme_prompt(user_note: str = "") -> str:
    base = (
        "Transform this image into an absurd, chaotic, low-IQ internet meme. "
        "Add exaggerated facial expressions with bulging eyes and shocked expressions. "
        "Add chaotic ALL-CAPS Impact font captions at top and bottom with ironic Gen-Z brainrot humor like 'no cap fr fr', 'based', 'L ratio'. "
        "Oversaturate all colors to neon extremes. Add deep-fried JPEG compression artifacts. "
        "Draw random red circles and red arrows pointing at irrelevant things. "
        "Scatter emojis 🧠💀🔥😭🤣🚀 across the image. "
        "Slightly bad cropping and rotation. "
        "Add 'RETARD COIN $RTRD' watermark in Comic Sans font at the bottom. "
        "The result should look made in MS Paint by a caffeinated raccoon at 3am."
    )
    if user_note:
        base += f" Additional instruction: {user_note}"
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
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]
    user_note = request.form.get("note", "")

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed"}), 400

    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    upload_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(upload_path)

    prompt = build_meme_prompt(user_note)
    result = call_fal_flux(upload_path, prompt)

    # Clean up upload
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
        "model": "fal-ai/flux-kontext-pro"
    })


if __name__ == "__main__":
    print("🧠 RETARD COIN Meme Generator starting on http://localhost:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
