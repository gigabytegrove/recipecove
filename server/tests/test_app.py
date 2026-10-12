import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["BRIDGE_TOKEN"] = "1234567890abcdefghijklmnopqrstuv"

from fastapi.testclient import TestClient
import app as media

client = TestClient(media.app)
HEADERS = {"Authorization": "Bearer 1234567890abcdefghijklmnopqrstuv"}


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["version"] == "1.0.0"


def test_auth_required():
    assert client.post("/extract/instagram", json={"url": "https://www.instagram.com/reel/ABC123/"}).status_code == 401
    assert client.post("/extract/instagram", headers={"Authorization": "Bearer wrong"}, json={"url": "https://www.instagram.com/reel/ABC123/"}).status_code == 401
    assert client.post("/extract/video", files={"file": ("clip.mp4", b"dummy", "video/mp4")}).status_code == 401


def test_valid_instagram_url_normalization():
    assert media.validate_instagram_url("https://instagram.com/reel/ABC123/?utm_source=abc") == "https://www.instagram.com/reel/ABC123/"
    assert media.validate_instagram_url("https://www.instagram.com/p/ABC123/") == "https://www.instagram.com/p/ABC123/"


def test_reject_internal_or_fake_instagram():
    bad = ["http://www.instagram.com/reel/ABC123/", "https://instagram.com.evil.com/reel/ABC123/", "https://127.0.0.1/reel/ABC123/", "https://www.instagram.com@127.0.0.1/reel/ABC123/", "https://www.instagram.com/redirect?url=http://127.0.0.1", "file:///etc/passwd", "https://www.instagram.com/reel/ABC123/../../"]
    for url in bad:
        try:
            media.validate_instagram_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Should reject {url}")


def test_url_rejected_before_downloader():
    response = client.post("/extract/instagram", headers=HEADERS, json={"url": "https://localhost/reel/ABC123/"})
    assert response.status_code == 422


def test_video_mime_rejected():
    response = client.post("/extract/video", headers=HEADERS, files={"file": ("bad.txt", b"not video", "text/plain")})
    assert response.status_code == 415


def test_video_max_bytes():
    class FauxUpload:
        file = None
        def __init__(self):
            self.file = tempfile.TemporaryFile()
            self.file.write(b"a" * (media.MAX_UPLOAD + 1))
            self.file.seek(0)

    upload = FauxUpload()
    try:
        try:
            media.process_local_file(upload)
        except ValueError as e:
            assert "80 MB" in str(e)
        else:
            raise AssertionError("Oversize media accepted")
    finally:
        upload.file.close()


def test_pipeline_text():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(media, "duration_of", return_value=42), patch.object(media, "transcribe_audio", return_value="one cup of flour"), patch.object(media, "ocr_video", return_value="Bake 25 minutes"):
            data = media.process_video(Path(tmp) / "dummy.mp4", Path(tmp), "My cake", "https://example.com")
            assert "My cake" in data["text"]
            assert "one cup of flour" in data["text"]
            assert "Bake 25 minutes" in data["text"]
            assert data["has_caption"] is True
            assert data["duration_seconds"] == 42


def test_processing_failure_does_not_claim_recipe():
    with patch.object(media, "extract_instagram", side_effect=ValueError("blocked")):
        response = client.post("/extract/instagram", headers=HEADERS, json={"url": "https://instagram.com/reel/ABC123/"})
        assert response.status_code == 422
        assert response.json()["detail"] == "blocked"


def test_short_video_keeps_first_frame():
    import shutil
    if not all(shutil.which(name) for name in ("ffmpeg", "ffprobe", "tesseract")):
        import pytest
        pytest.skip("Media tools unavailable in this test environment")
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        video = folder / "recipe.mp4"
        result = __import__("subprocess").run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=white:s=1280x720:d=4:r=3",
            "-vf", "drawtext=text='2 CUPS FLOUR':fontcolor=black:fontsize=95:x=140:y=250",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "4", "-y", str(video)
        ], capture_output=True)
        if result.returncode != 0:
            import pytest
            pytest.skip("FFmpeg installation lacks drawtext/libx264")
        assert media.duration_of(video) == 4
        assert "CUPS FLOUR" in media.ocr_video(video, folder)
