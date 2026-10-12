"""Regression tests for missing Facebook video speech and honest extraction statuses."""
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import media
import recipes
import transcribe_worker


def test_caption_can_not_hide_speech_worker_failure(tmp_path):
    with patch.object(media, "duration_of", return_value=60.0), \
         patch.object(media, "transcribe_audio", side_effect=RuntimeError("Whisper was killed (likely memory limit)")), \
         patch.object(media, "ocr_video", return_value=""):
        info = media.process_video(tmp_path/"video.mp4", tmp_path, caption="Slow cooker chicken recipe")
    assert info["extraction"]["video"] == "Downloaded and processed"
    assert "memory limit" in info["extraction"]["speech"]
    assert not info["has_speech"]
    assert "SPOKEN WORDS FROM VIDEO" not in info["text"]
    assert "POST CAPTION" in info["text"]
    assert "No speech transcript" in info["warning"]


def test_successful_speech_is_kept_separate_from_caption(tmp_path):
    with patch.object(media, "duration_of", return_value=35.0), \
         patch.object(media, "transcribe_audio", return_value="Cook for four hours."), \
         patch.object(media, "ocr_video", return_value="two cups broth"):
        info = media.process_video(tmp_path/"video.mp4", tmp_path, caption="Family dinner")
    assert "SPOKEN WORDS FROM VIDEO:\nCook for four hours." in info["text"]
    assert info["extraction"]["speech"] == "Transcribed spoken words"
    assert info["extraction"]["onscreen"] == "Read on-screen text"
    assert info["has_speech"]


def test_caption_only_fallback_is_explicit(tmp_path):
    import json
    def cmd(argv, **kwargs):
        if "--dump-single-json" in argv:
            return SimpleNamespace(stdout=json.dumps({"title":"Dinner", "description":"Ingredients\n2 cups broth\n1 lb chicken\nDirections\nCook this in a slow cooker for 6 hours."}))
        raise ValueError("Forbidden")
    with patch.object(media,"run_command",side_effect=cmd):
        out=media.extract_facebook("https://www.facebook.com/videos/12345678/")
    assert out["extraction"]["video"].startswith("Not downloaded")
    assert out["extraction"]["speech"] == "Not attempted — no video"
    assert out["has_speech"] is False


def test_no_caption_and_no_recognized_content_is_an_error(tmp_path):
    import pytest
    with patch.object(media,"duration_of",return_value=2.0), \
         patch.object(media,"transcribe_audio",return_value=""), \
         patch.object(media,"ocr_video",return_value=""):
        with pytest.raises(ValueError, match="no caption, recognized speech"):
            media.process_video(tmp_path/"video.mp4",tmp_path)


def test_worker_retries_without_vad_when_first_pass_blank(tmp_path):
    import os
    wav=tmp_path/"speech.wav"
    wav.write_bytes(b"RIFF"+b"0"*3000)
    filters=[]
    class FakeModel:
        def __init__(self,*args,**kwargs):
            assert kwargs["cpu_threads"] == 1
        def transcribe(self,*args,**kwargs):
            filters.append(kwargs["vad_filter"])
            return iter([] if kwargs["vad_filter"] else [SimpleNamespace(text="chicken and broth")]), {}
    fake=ModuleType("faster_whisper")
    fake.WhisperModel=FakeModel
    with patch.dict(sys.modules,{"faster_whisper":fake}), patch.object(sys,"argv",["transcribe_worker.py",str(wav)]):
        from io import StringIO
        with patch.object(sys,"stdout",new_callable=StringIO) as stdout:
            assert transcribe_worker.main() == 0
            assert stdout.getvalue().strip() == "chicken and broth"
    assert filters == [True,False]


def test_recipe_model_can_serialize_extraction_status():
    r=recipes.Recipe(title="Dinner",extraction={"speech":"No spoken words recognized"})
    assert r.model_dump()["extraction"]["speech"] == "No spoken words recognized"
