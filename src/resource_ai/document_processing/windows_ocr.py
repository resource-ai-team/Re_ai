"""Explicit local image adapter for Windows with installed Korean recognition pack."""

import json
import os
import subprocess
import sys
from pathlib import Path


class WindowsLocalOCR:
    def extract(self, content, media_type):
        if sys.platform != "win32" or media_type not in {"image/png", "image/jpeg"}:
            return {"status": "local_image_ocr_unavailable", "provider": "none", "pages": []}
        executable = (
            Path(os.environ.get("SystemRoot", "C:/Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
            result = subprocess.run(
                [
                    str(executable),
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(Path(__file__).with_suffix(".ps1")),
                ],
                input=content,
                capture_output=True,
                timeout=30,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if len(result.stdout) > 500000:
                raise ValueError("bounded extraction")
            return json.loads(result.stdout.decode("utf-8-sig"))
        except (OSError, ValueError, subprocess.SubprocessError):
            return {
                "status": "local_ocr_unavailable_or_timed_out",
                "provider": "windows-local-ocr",
                "pages": [],
            }
