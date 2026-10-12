"""One-shot local Whisper worker: intentionally never loaded by the API server."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print("Expected a valid WAV path", file=sys.stderr)
        return 2
    # Import ML libraries only after the child process has started, so the
    # long-running FastAPI process retains no Whisper/CTranslate2 allocations.
    from faster_whisper import WhisperModel

    model = WhisperModel(
        os.getenv("WHISPER_MODEL", "base"),
        device="cpu",
        compute_type="int8",
        cpu_threads=1,
        num_workers=1,
        download_root="/models",
    )
    # VAD can incorrectly reject quiet narration, especially in cooking Reels.
    # Retry only empty results without VAD, using the already loaded model.
    segments, _ = model.transcribe(sys.argv[1], vad_filter=True, beam_size=3)
    words = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    if not words.strip():
        segments, _ = model.transcribe(sys.argv[1], vad_filter=False, beam_size=3)
        words = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    print(words[:47000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
