"""Self-hosted video extraction used by Recipe Importer."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, UploadFile



VERSION = "1.2.1"
MAX_UPLOAD = 80 * 1024 * 1024
MAX_SECONDS = 900
MAX_EXTRACT_CHARS = 47000
MAX_OCR_FRAMES = 18
WHISPER_TIMEOUT_SECONDS = int(os.getenv("WHISPER_TIMEOUT_SECONDS", "780"))
if not 60 <= WHISPER_TIMEOUT_SECONDS <= 3600:
    raise ValueError("WHISPER_TIMEOUT_SECONDS must be between 60 and 3600")
LOG = logging.getLogger(__name__)
COOKIE_PATH = Path(os.getenv("INSTAGRAM_COOKIES_FILE", "/run/secrets/instagram_cookies.txt"))

_pipeline_limit = threading.BoundedSemaphore(value=1)


def validate_instagram_url(url: str) -> str:
    """Only let yt-dlp handle well-formed public Instagram post/reel URLs."""
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("Only https://www.instagram.com/reel/... or /p/... links are supported")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Invalid Instagram URL")
    match = re.fullmatch(r"/(reel|p|tv)/([A-Za-z0-9_-]{5,40})/?", parsed.path)
    if not match:
        raise ValueError("Expected an Instagram Reel or post URL")
    return f"https://www.instagram.com/{match.group(1)}/{match.group(2)}/"


def run_command(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Media processing timed out") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("A required media utility is not installed") from exc
    if result.returncode != 0:
        # Avoid exposing command-line arguments, cookies or stdout to end users.
        raise ValueError(f"Media processor exited with code {result.returncode}")
    return result


def duration_of(path: Path) -> float:
    r = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], timeout=25)
    try:
        duration = float(r.stdout.strip())
    except ValueError as exc:
        raise ValueError("Unsupported or damaged video") from exc
    if not 0 < duration <= MAX_SECONDS:
        raise ValueError("Video must be between 1 second and 15 minutes")
    return duration


def transcribe_audio(path: Path) -> str:
    """Isolate the memory-heavy speech model in a disposable child process.

    The API server never imports faster_whisper or retains the model after a
    request. When the child exits, the OS reclaims its address space even if
    the underlying native inference libraries retain memory internally.
    """
    with tempfile.TemporaryDirectory(prefix="ri_audio_") as temp:
        audio = Path(temp) / "speech.wav"
        try:
            result = subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-threads", "1", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)
            ], capture_output=True, timeout=120, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeError("Could not prepare video audio") from exc
        if result.returncode != 0 or not audio.exists() or audio.stat().st_size < 1024:
            return ""
        try:
            transcription = subprocess.run([
                sys.executable, str(Path(__file__).with_name("transcribe_worker.py")), str(audio)
            ], capture_output=True, text=True, timeout=WHISPER_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Speech recognition exceeded the configured processing timeout") from exc
        except OSError as exc:
            raise RuntimeError("Could not start speech recognition") from exc
        if transcription.returncode != 0:
            LOG.warning("Whisper worker exited with status %d", transcription.returncode)
            raise RuntimeError("Speech recognition worker failed; check memory limit and Whisper model")
        return transcription.stdout.strip()[:MAX_EXTRACT_CHARS]


def ocr_video(path: Path, workdir: Path) -> str:
    frames = workdir / "frames"
    frames.mkdir()
    run_command([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1", "-i", str(path),
        "-vf", "select='isnan(prev_selected_t)+gte(t-prev_selected_t,12)',scale='min(960,iw)':-2",
        "-fps_mode", "vfr", "-frames:v", str(MAX_OCR_FRAMES),
        str(frames / "frame_%03d.png")
    ], timeout=150)
    seen: set[str] = set()
    lines: list[str] = []
    for frame in sorted(frames.glob("frame_*.png")):
        ocr = run_command(["tesseract", str(frame), "stdout", "-l", "eng"], timeout=25).stdout
        for line in ocr.splitlines():
            cleaned = " ".join(line.split()).strip("•- ")
            signature = re.sub(r"[^a-z0-9]", "", cleaned.lower())
            if len(signature) < 4 or signature in seen:
                continue
            seen.add(signature)
            lines.append(cleaned)
    return "\n".join(lines)[:MAX_EXTRACT_CHARS]


def process_video(path: Path, workdir: Path, caption: str = "", source_url: str = "") -> dict:
    seconds = duration_of(path)
    limitations: list[str] = []
    try:
        speech = transcribe_audio(path)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Speech extraction unavailable: %s", exc)
        speech = ""
        limitations.append("Speech recognition failed; check Whisper model/network access")
    try:
        visible = ocr_video(path, workdir)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Video OCR unavailable: %s", exc)
        visible = ""
        limitations.append("On-screen text recognition failed")
    caption = caption.strip()[:20000]
    if not (caption or speech or visible):
        raise ValueError("No readable caption, speech or on-screen text was found; share a screenshot or paste recipe text instead")
    # Explicit section labels help the recipe parser distinguish sources.
    chunks = []
    if caption:
        chunks.append("POST CAPTION / DESCRIPTION:\n" + caption)
    if speech:
        chunks.append("SPOKEN WORDS FROM VIDEO:\n" + speech)
    if visible:
        chunks.append("TEXT VISIBLE IN VIDEO FRAMES:\n" + visible)
    # Put the likely title first: the built-in no-AI parser uses line one as
    # the recipe title, while configured AI sees all labeled evidence below.
    first_caption_line = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    suggested_title = (first_caption_line or "Review this video recipe")[:140]
    combined = (suggested_title + "\n\n" + "\n\n".join(chunks))[:MAX_EXTRACT_CHARS]
    return {
        "text": combined, "source_url": source_url,
        "has_caption": bool(caption), "has_speech": bool(speech), "has_visible_text": bool(visible),
        "duration_seconds": round(seconds, 1),
        "warning": "Video extraction is imperfect. Review quantities, ingredients and steps before saving." + (" " + "; ".join(limitations) if limitations else "")
    }


def extract_instagram(url: str) -> dict:
    clean_url = validate_instagram_url(url)
    with tempfile.TemporaryDirectory(prefix="bridge_instagram_") as temp:
        workdir = Path(temp)
        args = ["yt-dlp", "--no-playlist", "--no-warnings", "--no-progress"]
        if COOKIE_PATH.is_file():
            args.extend(["--cookies", str(COOKIE_PATH)])
        try:
            meta = run_command(args + ["--dump-single-json", "--skip-download", clean_url], timeout=90)
            info = json.loads(meta.stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Instagram could not be read. Public posts may work; private/login-required or blocked Reels need a locally shared video file") from exc
        if info.get("_type") == "playlist":
            raise ValueError("Only single video posts can be imported")
        title = str(info.get("title") or "")
        desc = str(info.get("description") or "")
        caption = desc if desc and desc != title else title
        filename = workdir / "source.%(ext)s"
        run_command(args + [
            "--max-filesize", "80M", "--format", "best[ext=mp4]/best",
            "--output", str(filename), clean_url
        ], timeout=240)
        videos = [p for p in workdir.glob("source.*") if p.is_file()]
        if len(videos) != 1 or videos[0].stat().st_size > MAX_UPLOAD:
            raise ValueError("Instagram video was not downloaded or exceeded 80 MB")
        return process_video(videos[0], workdir, caption, clean_url)


def process_local_file(upload: UploadFile) -> dict:
    with tempfile.TemporaryDirectory(prefix="bridge_upload_") as temp:
        workdir = Path(temp)
        media = workdir / "upload.video"
        total = 0
        with media.open("wb") as out:
            while True:
                block = upload.file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_UPLOAD:
                    raise ValueError("Video exceeds the 80 MB upload limit")
                out.write(block)
        if not total:
            raise ValueError("Empty video")
        return process_video(media, workdir)


def run_exclusive(func, *args):
    if not _pipeline_limit.acquire(blocking=False):
        raise HTTPException(429, "A video is already being processed. Try again when it completes")
    try:
        return func(*args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, OSError, subprocess.SubprocessError):
        raise HTTPException(502, "Video processor failed. Review server logs and installed media tools")
    finally:
        _pipeline_limit.release()


