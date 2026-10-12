import base64
import logging
import os
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dreamforge")

app = FastAPI(title="DreamForge AI", version="2.0.0")

PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0c0b13">
<title>DreamForge AI — Image & Video Studio</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0c0b13;color:#f8f6ff;font-family:Inter,system-ui,-apple-system,sans-serif;min-height:100vh}
main{width:min(980px,100%);margin:auto;padding:24px 16px 45px}
header{text-align:center;padding:24px 0 28px}
.logo{font-size:clamp(29px,6vw,43px);font-weight:850;letter-spacing:-1.7px}
.logo span{color:#b99aff}.tag{color:#aaa5ba;margin-top:9px;font-size:15px}
.tabs{display:flex;gap:8px;margin:0 0 14px}
.tab{flex:1;padding:12px;border:1px solid #393348;border-radius:11px;background:#171521;color:#cfc6e8;font-weight:750;cursor:pointer}
.tab.active{background:#8d62f2;color:#fff;border-color:#8d62f2}
.panel{background:#171521;border:1px solid #302b40;border-radius:22px;padding:clamp(17px,4vw,30px);box-shadow:0 18px 65px #0003}
label{display:block;font-weight:700;margin:15px 0 10px}
textarea{width:100%;min-height:125px;resize:vertical;padding:15px;border:1px solid #393348;border-radius:13px;background:#0e0d16;color:white;font:inherit;line-height:1.6}
textarea:focus,select:focus{outline:2px solid #9b76ff;border-color:transparent}
.controls{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}
.field{flex:1;min-width:130px}
select{width:100%;padding:12px;border-radius:10px;border:1px solid #393348;background:#0e0d16;color:white;font:inherit}
button{width:100%;margin-top:18px;padding:15px;border:0;border-radius:12px;background:linear-gradient(100deg,#8657f5,#c06cf4);color:white;font-size:16px;font-weight:750;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.hint{color:#aaa5ba;font-size:12px;line-height:1.6;margin-top:12px}
.status{margin:18px 0 0;text-align:center;color:#d1bfff;line-height:1.6;overflow-wrap:anywhere}
.media{width:100%;height:auto;display:block;margin-top:20px;border-radius:14px;background:#0e0d16}
.actions{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin-top:14px}
.actions a,.download{color:#d4c3ff;font-weight:650}
.episode{border-top:1px solid #393348;margin-top:22px;padding-top:12px}
footer{text-align:center;color:#817b91;font-size:12px;margin-top:25px}
.hidden{display:none!important}
</style>
</head>
<body>
<main>
<header>
  <div class="logo">✦ DreamForge <span>AI</span></div>
  <div class="tag">Turn your imagination into images and videos.</div>
</header>

<div class="tabs" role="tablist">
  <button class="tab active" id="imageTab" type="button">🖼️ Images</button>
  <button class="tab" id="videoTab" type="button">🎬 Videos & Series</button>
</div>

<section class="panel" id="imagePanel">
  <h2>AI Image Generator</h2>
  <label for="prompt">Describe your image</label>
  <textarea id="prompt" maxlength="700" placeholder="A magical castle above the clouds at sunset, cinematic lighting, beautiful fantasy art..."></textarea>
  <div class="controls">
    <div class="field">
      <label for="style">Art style</label>
      <select id="style">
        <option value="">No extra style</option>
        <option value="photorealistic, detailed photography">Realistic photo</option>
        <option value="cinematic lighting, cinematic composition">Cinematic</option>
        <option value="digital fantasy illustration, intricate details">Fantasy art</option>
        <option value="anime illustration, expressive colors">Anime</option>
        <option value="3D render, polished materials">3D render</option>
        <option value="watercolor painting, artistic brushwork">Watercolor</option>
      </select>
    </div>
  </div>
  <button id="generateImage" type="button">✦ Generate image</button>
  <div class="hint">Image generation uses your configured Cloudflare Workers AI model and may incur charges.</div>
  <div id="imageStatus" class="status" role="status" aria-live="polite"></div>
  <img id="imageResult" class="media hidden" alt="Your AI-generated creation">
  <div class="actions"><a id="imageDownload" class="hidden" href="#" download="dreamforge-creation.png">Download image</a></div>
</section>

<section class="panel hidden" id="videoPanel">
  <h2>AI Video & Series Creator</h2>
  <p class="hint">Create a short vertical clip or generate several connected episode prompts. Each episode is generated separately; exact character consistency is not guaranteed.</p>
  <label for="videoPrompt">Video story or series idea</label>
  <textarea id="videoPrompt" maxlength="2200" placeholder="A young explorer discovers a magical forest, glowing plants, mysterious footprints, cinematic fantasy..."></textarea>
  <div class="controls">
    <div class="field">
      <label for="episodeCount">Series length</label>
      <select id="episodeCount">
        <option value="1">Single video</option>
        <option value="3">3 episodes</option>
        <option value="5">5 episodes</option>
      </select>
    </div>
    <div class="field">
      <label for="videoDuration">Duration per episode</label>
      <select id="videoDuration">
        <option value="5">5 seconds</option>
        <option value="8">8 seconds</option>
        <option value="10">10 seconds</option>
        <option value="15">15 seconds</option>
      </select>
    </div>
    <div class="field">
      <label for="videoRatio">Format</label>
      <select id="videoRatio">
        <option value="9:16">Vertical 9:16 (Reels)</option>
        <option value="16:9">Landscape 16:9</option>
        <option value="1:1">Square 1:1</option>
        <option value="4:3">4:3</option>
        <option value="3:4">3:4</option>
      </select>
    </div>
    <div class="field">
      <label for="videoResolution">Resolution</label>
      <select id="videoResolution">
        <option value="720P">720p</option>
        <option value="1080P">1080p</option>
      </select>
    </div>
  </div>
  <button id="createSeries" type="button">🎬 Create Video Series</button>
  <div id="videoStatus" class="status" role="status" aria-live="polite"></div>
  <div id="videoResults"></div>
  <div class="hint">Video generation can be slower and more expensive than image generation. Check Cloudflare model availability and billing before use.</div>
</section>
<footer>DreamForge AI · Made for your imagination</footer>
</main>
<script>
const $ = id => document.getElementById(id);
$("imageTab").addEventListener("click", () => {
  $("imagePanel").classList.remove("hidden"); $("videoPanel").classList.add("hidden");
  $("imageTab").classList.add("active"); $("videoTab").classList.remove("active");
});
$("videoTab").addEventListener("click", () => {
  $("videoPanel").classList.remove("hidden"); $("imagePanel").classList.add("hidden");
  $("videoTab").classList.add("active"); $("imageTab").classList.remove("active");
});

let previousImageUrl = null;
$("generateImage").addEventListener("click", async () => {
  const prompt = $("prompt").value.trim();
  if (!prompt) { $("imageStatus").textContent = "Please describe the image you want."; $("prompt").focus(); return; }
  const button = $("generateImage");
  button.disabled = true; button.textContent = "Creating your image...";
  $("imageStatus").textContent = "Generating your image. This may take a little while.";
  $("imageResult").classList.add("hidden"); $("imageDownload").classList.add("hidden");
  try {
    const style = $("style").value;
    const response = await fetch("/generate", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({prompt: style ? prompt + ", " + style : prompt})
    });
    if (!response.ok) {
      let message = "Generation failed. Please try again.";
      try { const data = await response.json(); message = data.detail || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    if (!blob.type.startsWith("image/")) throw new Error("The AI provider did not return an image.");
    if (previousImageUrl) URL.revokeObjectURL(previousImageUrl);
    previousImageUrl = URL.createObjectURL(blob);
    $("imageResult").src = previousImageUrl;
    $("imageResult").classList.remove("hidden");
    $("imageDownload").href = previousImageUrl;
    $("imageDownload").download = "dreamforge-creation." + (blob.type === "image/jpeg" ? "jpg" : "png");
    $("imageDownload").classList.remove("hidden");
    $("imageStatus").textContent = "Your creation is ready!";
  } catch (error) { $("imageStatus").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "✦ Generate image"; }
});

$("createSeries").addEventListener("click", async () => {
  const idea = $("videoPrompt").value.trim();
  const count = Number($("episodeCount").value);
  const duration = Number($("videoDuration").value);
  const ratio = $("videoRatio").value;
  const resolution = $("videoResolution").value;
  if (!idea) { $("videoStatus").textContent = "Enter your video story first."; $("videoPrompt").focus(); return; }

  const button = $("createSeries");
  button.disabled = true; $("videoResults").replaceChildren();
  try {
    for (let episode = 1; episode <= count; episode++) {
      $("videoStatus").textContent = `Generating episode ${episode} of ${count}...`;
      const episodePrompt = `${idea}. Episode ${episode} of ${count}. Maintain the same protagonist, clothing, environment, color palette, and cinematic style. ` +
        (episode === 1 ? "Introduce the main character and begin the story." :
        `Continue from the previous episode with a new event, preserving story continuity. This episode should move the story forward.`);
      const response = await fetch("/generate-video", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({prompt:episodePrompt, duration, ratio, resolution})
      });
      let data;
      try { data = await response.json(); } catch (_) { data = {}; }
      if (!response.ok) throw new Error(data.detail || `Episode ${episode} failed.`);
      if (!data.video_url) throw new Error(`No video URL returned for episode ${episode}.`);

      const card = document.createElement("div"); card.className = "episode";
      const title = document.createElement("h3"); title.textContent = `Episode ${episode}`;
      const video = document.createElement("video"); video.className = "media";
      video.src = data.video_url; video.controls = true; video.playsInline = true; video.preload = "metadata";
      const actions = document.createElement("div"); actions.className = "actions";
      const link = document.createElement("a"); link.href = data.video_url; link.target = "_blank";
      link.rel = "noopener noreferrer"; link.textContent = "Open / save video";
      actions.appendChild(link); card.append(title, video, actions); $("videoResults").appendChild(card);
    }
    $("videoStatus").textContent = "Your series is ready!";
  } catch (error) { $("videoStatus").textContent = error.message; }
  finally { button.disabled = false; }
});
</script>
</body>
</html>
"""


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=700)


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2500)
    duration: int = Field(default=5, ge=3, le=15)
    ratio: str = Field(default="9:16")
    resolution: str = Field(default="720P")


ALLOWED_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
ALLOWED_RESOLUTIONS = {"720P", "1080P"}


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(PAGE)


@app.get("/health")
def health():
    return {"status": "ok", "app": "DreamForge AI", "version": "2.0.0"}


def detect_media_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


@app.post("/generate")
def generate_image(request: GenerateRequest):
    token = os.getenv("CF_API_TOKEN")
    account_id = os.getenv("CF_ACCOUNT_ID")
    if not token or not account_id:
        raise HTTPException(
            status_code=503,
            detail="AI service is not configured. Add CF_API_TOKEN and CF_ACCOUNT_ID in Render Environment settings."
        )

    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Please enter an image description.")

    model = os.getenv("CF_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    try:
        with httpx.Client(timeout=180) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"prompt": prompt, "steps": 4},
            )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("image/"):
            image_bytes = response.content
        else:
            data = response.json()
            encoded = data.get("result", {}).get("image")
            if not encoded:
                raise ValueError("Image missing from provider response")
            image_bytes = base64.b64decode(encoded)
        return Response(
            content=image_bytes,
            media_type=detect_media_type(image_bytes),
            headers={"Cache-Control": "no-store"}
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Image generation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="AI image generation failed. Check Render logs, Cloudflare token, account ID, and image model access."
        ) from None


@app.post("/generate-video")
def generate_video(request: VideoRequest):
    token = os.getenv("CF_API_TOKEN")
    account_id = os.getenv("CF_ACCOUNT_ID")
    if not token or not account_id:
        raise HTTPException(
            status_code=503,
            detail="Video service is not configured. Add CF_API_TOKEN and CF_ACCOUNT_ID in Render Environment settings."
        )
    if request.ratio not in ALLOWED_RATIOS:
        raise HTTPException(status_code=400, detail="Invalid aspect ratio.")
    if request.resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Invalid resolution.")

    # Cloudflare's current REST API for third-party video models uses a model
    # field and an input object at /ai/run.
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
    model = os.getenv("CF_VIDEO_MODEL", "alibaba/hh1.1-t2v")
    payload = {
        "model": model,
        "input": {
            "prompt": request.prompt.strip(),
            "duration": request.duration,
            "ratio": request.ratio,
            "resolution": request.resolution,
        },
    }

    try:
        with httpx.Client(timeout=300) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        video_url = data.get("result", {}).get("video")
        if not data.get("success", True) or not video_url:
            logger.error("Video provider returned unsuccessful response")
            raise ValueError("Video URL missing from provider response")
        parsed = urlparse(video_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Provider returned an invalid video URL")
        return {"video_url": video_url, "model": model}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Video generation failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="AI video generation failed. Check Cloudflare model availability, account billing, token permissions, and Render logs."
        ) from None


@app.get("/download-video")
def download_video(url: str = Query(..., min_length=10, max_length=2000)):
    """Redirect only to HTTPS video URLs from Cloudflare's published video hosts."""
    parsed = urlparse(url)
    allowed_hosts = {
        "examples.aig.cloudflare.com",
        "pub-04a6d208d361438ea01b797e6973bd19.r2.dev",
    }
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Unsupported video download URL.")
    return RedirectResponse(url=url, status_code=302)
