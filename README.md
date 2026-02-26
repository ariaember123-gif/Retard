# 🧠 RETARD COIN — Website + Meme Generator

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your fal.ai API key
```bash
export FAL_API_KEY=your_fal_ai_key_here
```

### 3. Run the Flask server
```bash
python app.py
```

Open http://localhost:5000

---

## File Structure
```
retardcoin/
├── app.py                  # Flask backend
├── index.html              # Main website
├── meme_generator.html     # Meme generator UI
├── requirements.txt
└── README.md
```

## Routes
- `GET /`                  → Main website  
- `GET /meme_generator`    → Meme Generator page  
- `POST /api/generate`     → Generate meme (multipart/form-data: image, note)  
- `GET /api/health`        → Health check

## API: POST /api/generate
**Form fields:**
- `image` — image file (PNG/JPG/WEBP, max 10MB)
- `note` — optional extra instructions

**Response:**
```json
{
  "image_url": "https://...",
  "prompt": "..."
}
```

## fal.ai Model
Uses `fal-ai/flux-kontext-pro` for image-to-image transformation.
Get your key at https://fal.ai

## Environment Variables
| Variable | Description |
|----------|-------------|
| `FAL_API_KEY` | Your fal.ai API key |
