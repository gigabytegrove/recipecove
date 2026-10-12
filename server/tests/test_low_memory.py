"""Guards against retaining heavy speech inference in the long-running API."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import media


def test_transcribe_is_disposable_subprocess():
    with tempfile.TemporaryDirectory() as temp:
        video = Path(temp) / "example.mp4"
        video.write_bytes(b"placeholder")
        invocations = []

        def fake_run(argv, **kwargs):
            invocations.append((argv, kwargs))
            if argv[0] == "ffmpeg":
                Path(argv[-1]).write_bytes(b"RIFF" + b"\0" * 1400)
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0, stdout="2 cups flour.\n", stderr="")

        with patch.object(media.subprocess, "run", side_effect=fake_run):
            assert media.transcribe_audio(video) == "2 cups flour."
        assert len(invocations) == 2
        argv, opts = invocations[1]
        assert argv[0] == sys.executable
        assert argv[1].endswith("transcribe_worker.py")
        assert Path(argv[2]).name == "speech.wav"
        assert opts["timeout"] == media.WHISPER_TIMEOUT_SECONDS
        assert "faster_whisper" not in media.__dict__
        assert not hasattr(media, "_whisper_model")


def test_failed_worker_reports_failure_without_retaining_model():
    with tempfile.TemporaryDirectory() as temp:
        video = Path(temp) / "example.mp4"
        video.write_bytes(b"placeholder")
        def fake_run(argv, **kwargs):
            if argv[0] == "ffmpeg":
                Path(argv[-1]).write_bytes(b"RIFF" + b"\0" * 1400)
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=137, stdout="", stderr="Killed")
        with patch.object(media.subprocess, "run", side_effect=fake_run):
            try:
                media.transcribe_audio(video)
                assert False, "Expected failure"
            except RuntimeError as exc:
                assert "worker failed" in str(exc)
        assert not hasattr(media, "_whisper_model")


def test_compose_resource_guards_and_disk_tmp():
    import yaml
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "compose.yaml").read_text())
    app = config["services"]["recipe-importer"]
    assert app["cpus"] <= 1.0
    assert app["mem_limit"] == "1536m"
    assert app["memswap_limit"] == app["mem_limit"]
    assert not app.get("tmpfs")
    assert any(volume.endswith(":/tmp") for volume in app["volumes"])
    assert app["environment"]["OMP_NUM_THREADS"] == "1"
