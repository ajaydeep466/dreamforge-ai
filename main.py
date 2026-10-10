
import os
from io import BytesIO
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from huggingface_hub import InferenceClient

app = FastAPI(title="DreamForge AI")

PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DreamForge AI — Create Anything</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0c0b13;color:#f8f6ff;
font-family:system-ui,-apple-system,sans-serif}
main{max-width:900px;margin:auto;padding:40px 20px}
header{text-align:center;padding:30px 0}
.logo{font-size:34px;font-weight:850;letter-spacing:-1.5px}
.logo span{color:#b99aff}
.tag{color:#aaa5ba;margin-top:8px}
.panel{background:#171521;border:1px solid #302b40;
border-radius:20px;padding:22px}
label{display:block;margin-bottom:10px;font-weight:650}
textarea{width:100%;min-height:120px;resize:vertical;
padding:15px;border:1px solid #393348;border-radius:12px;
background:#0e0d16;color:white;font:inherit}
textarea:focus{outline:2px solid #9b76ff}
button{width:100%;margin-top:15px;padding:14px;border:0;
border-radius:12px;background:linear-gradient(100deg,#8657f5,#c06cf4);
color:white;font-size:16px;font-weight:750;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.note{color:#aaa5ba;font-size:12px;line-height:1.6}
#status{margin:18px 0;text-align:center;color:#c8b7ff}
#result{width:100%;display:none;margin-top:18px;border-radius:14px}
.actions{display:flex;gap:12px;align-items:center;margin-top:12px}
.actions a{color:#d4c3ff}
footer{text-align:center;color:#817b91;font-size:12px;margin:28px}
</style>
</head>
<body>
<main>
<header>
<div class="logo">✦ DreamForge <span>AI</span></div>
<div class="tag">Turn your imagination into images.</div>
</header>
<section class="panel">
<label for="prompt">Describe the image you want</label>
<textarea id="prompt" maxlength="500"
placeholder="A magical castle above the clouds at sunset, cinematic lighting, highly detailed..."></textarea>
<button id="generate">✦ Generate image</button>
<div class="note" style="margin-top:12px">
AI generation requires a configured server-side model token.
Generation may take time. Please don't submit sensitive information.
</div>
<div id="status" role="status"></div>
<img id="result" alt="Your AI-generated image">
<div class="actions">
<a id="download" href="#" download="dreamforge-creation.png"
style="display:none">Download image</a>
</div>
</section>
<footer>DreamForge AI · Made for your imagination</footer>
</main>
<script>
const button = document.getElementById("generate");
const promptBox = document.getElementById("prompt");
const statusBox = document.getElementById("status");
const result = document.getElementById("result");
const download = document.getElementById("download");
let previousUrl = null;

button.addEventListener("click", async () => {
  const prompt = promptBox.value.trim();
  if (!prompt) {
    statusBox.textContent = "Please describe an image first.";
    return;
  }

  button.disabled = true;
  button.textContent = "Creating your image...";
  statusBox.textContent = "Your image is being generated. Please wait.";
  result.style.display = "none";
  download.style.display = "none";

  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({prompt})
    });

    if (!response.ok) {
      let message = "Image generation failed. Please try again.";
      try {
        const data = await response.json();
        message = data.detail || message;
      } catch (_) {}
      throw new Error(message);
    }

    const blob = await response.blob();
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    previousUrl = URL.createObjectURL(blob);

    result.src = previousUrl;
    result.style.display = "block";
    download.href = previousUrl;
    download.style.display = "inline";
    statusBox.textContent = "Your creation is ready!";
  } catch (error) {
    statusBox.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "✦ Generate image";
  }
});
</script>
</body>
</html>
"""


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(PAGE)


@app.get("/health")
def health():
    return {"status": "ok", "app": "DreamForge AI"}


@app.post("/generate")
def generate_image(request: GenerateRequest):
    token = os.getenv("HF_TOKEN")
    if not token:
        raise HTTPException(
            status_code=503,
            detail="The AI service is not configured yet. Add HF_TOKEN in Render Environment settings."
        )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Enter an image description.")

    model = os.getenv(
        "HF_MODEL", "black-forest-labs/FLUX.1-schnell"
    )

    try:
        client = InferenceClient(
            model=model,
            token=token,
        )
        image = client.text_to_image(prompt)

        output = BytesIO()
        image.save(output, format="PNG")
        return Response(
            content=output.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"}
        )
    except Exception:
        # Do not expose API tokens or internal error details to visitors.
        raise HTTPException(
            status_code=502,
            detail="The AI provider could not generate the image. Check your Hugging Face token, model access, provider availability, and usage credits."
        )
