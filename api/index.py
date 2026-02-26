import os
import base64
import json
import time
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# /tmp is the ONLY writable directory on Vercel
UPLOAD_FOLDER = "/tmp/rtrd_uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# Flask must be told where the HTML files live relative to project root
# On Vercel, CWD is the project root, not the api/ folder
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, static_folder=ROOT_DIR, static_url_path="")
CORS(app)

FAL_KEY = os.environ.get("FAL_API_KEY", "")


def ensure_tmp():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def image_to_base64(path):
    with open(path, "rb") as f:
        data = f.read()
    ext = path.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def call_fal_flux(image_path: str, prompt: str) -> dict:
    if not FAL_KEY:
        return {"error": "FAL_API_KEY environment variable is not set. Add it in Vercel → Settings → Environment Variables."}

    headers = {
        "Authorization": f"Key {FAL_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": prompt,
        "image_url": image_to_base64(image_path),
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "jpeg",
    }

    # Submit to fal.ai queue
    try:
        resp = requests.post(
            "https://queue.fal.run/fal-ai/flux-kontext-pro",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return {"error": "fal.ai submit timed out after 30s"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error submitting to fal.ai: {str(e)}"}

    if resp.status_code not in (200, 201):
        return {"error": f"fal.ai rejected request [{resp.status_code}]: {resp.text[:300]}"}

    data = resp.json()
    request_id = data.get("request_id")

    # Immediate / synchronous result
    if not request_id:
        return _parse_result(data)

    # Poll — max 50s (leaves buffer before Vercel's 60s hard limit)
    status_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}/status"
    result_url = f"https://queue.fal.run/fal-ai/flux-kontext-pro/requests/{request_id}"

    for _ in range(50):
        time.sleep(1)
        try:
            sr = requests.get(status_url, headers=headers, timeout=10)
            if sr.status_code != 200:
                continue
            status = sr.json().get("status", "")
            if status == "COMPLETED":
                rr = requests.get(result_url, headers=headers, timeout=15)
                return _parse_result(rr.json())
            if status == "FAILED":
                return {"error": "fal.ai processing failed. Try again."}
        except requests.exceptions.RequestException:
            continue

    return {"error": "Timed out waiting for fal.ai (>50s). Try again — it may have been a busy moment."}


def _parse_result(data: dict) -> dict:
    images = data.get("images") or data.get("output", {}).get("images", []) or []
    if images:
        img = images[0]
        url = img.get("url") if isinstance(img, dict) else img
        if url:
            return {"image_url": url}
    return {"error": f"fal.ai returned no image. Raw response: {json.dumps(data)[:400]}"}


def build_prompt(intensity: str, user_note: str) -> str:
    intensity_map = {
        "mild": "slightly meme-ified with subtle humor",
        "full-retard": "absurd chaotic low-IQ internet meme energy with maximum brainrot",
        "nuclear": "EXTREME nuclear chaos, maximum distortion, completely unhinged and incomprehensible",
        "deep-fried": "deep-fried JPEG artifact aesthetic with blown-out neon oversaturation",
    }
    style = intensity_map.get(intensity, intensity_map["full-retard"])

    prompt = (
        f"Transform this image into {style}. "
        "Add exaggerated facial expressions with bulging eyes and shocked dumb expressions. "
        "Add chaotic ALL-CAPS Impact font captions at the top and bottom with ironic Gen-Z "
        "brainrot humor: 'no cap fr fr', 'based', 'L ratio', 'he ate', 'rizz', 'bussin'. "
        "Oversaturate all colors to neon extremes. "
        "Add deep-fried JPEG compression artifact aesthetic. "
        "Draw random red circles and red arrows pointing at completely irrelevant things. "
        "Scatter random emojis (brain, skull, fire, crying laughing, rocket) across the image. "
        "Apply slightly bad cropping and a few degrees of rotation. "
        "Add 'RETARD COIN $RTRD' watermark in Comic Sans at the bottom corner. "
        "The final result should look like it was made in MS Paint by a caffeinated raccoon at 3am."
    )
    if user_note:
        prompt += f" Also: {user_note}"
    return prompt


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.route("/meme_generator")
def meme_generator():
    return send_from_directory(ROOT_DIR, "meme_generator.html")


@app.route("/api/generate", methods=["POST"])
def generate_meme():
    ensure_tmp()

    if "image" not in request.files:
        return jsonify({"error": "No 'image' field in request"}), 400

    file = request.files["image"]
    if not file or not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Unsupported file type. Use PNG, JPG, or WEBP."}), 400

    intensity = request.form.get("intensity", "full-retard")
    user_note = request.form.get("note", "")

    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    upload_path = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(upload_path)
    except Exception as e:
        return jsonify({"error": f"Failed to save file to /tmp: {str(e)}"}), 500

    prompt = build_prompt(intensity, user_note)
    result = call_fal_flux(upload_path, prompt)

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
        "python": "3.x",
        "upload_dir": UPLOAD_FOLDER,
    })
