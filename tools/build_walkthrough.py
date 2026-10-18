#!/usr/bin/env python3
"""Assemble an edited, captioned walkthrough from actual browser captures.

This is a paced screen walkthrough, not a real-time performance recording.
Requires ffmpeg with drawtext support, or the optional imageio-ffmpeg package.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENES = (
    ("01-start.png", "FaciRob maps ROS 2 projects and flags potential\nintegration issues without building the repository."),
    ("02-example.png", "Start with an offline example.\nThe missing YAML reference is deliberately introduced."),
    ("03-finding.png", "RD302 identifies the missing parameter file.\nInspect source evidence and the repair guidance."),
    ("04-architecture.png", "Explore source definitions and launch instances.\nThis static map does not establish runtime behavior."),
    ("05-correction.png", "Review the supplied one-line correction:\nmissing.yaml becomes scan.yaml."),
    ("06-corrected.png", "The corrected source is scanned separately.\nRD302 is absent; build and runtime checks remain separate."),
    ("07-local-use.png", "Try a public Git URL or a local repository ZIP.\nAnalysis runs locally and does not execute project code."),
)


def timestamp(seconds: int) -> str:
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02},000"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--showcase-dir", type=Path, default=ROOT / "docs/showcase")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    executable = args.ffmpeg
    if not executable:
        try:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            parser.error("install ffmpeg or imageio-ffmpeg to encode the walkthrough")
    fonts = (Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    font = next((str(path) for path in fonts if path.is_file()), None)
    font_option = f"fontfile='{font}':" if font else ""
    with tempfile.TemporaryDirectory(prefix="robot-doctor-video-") as temporary:
        work = Path(temporary)
        clips = []
        subtitles = []
        for index, (filename, caption) in enumerate(SCENES):
            screenshot = args.showcase_dir / "screens" / filename
            if not screenshot.is_file():
                parser.error(f"missing actual browser capture: {screenshot}")
            caption_file = work / f"caption-{index}.txt"
            caption_file.write_text(caption, encoding="utf-8")
            clip = work / f"scene-{index}.mp4"
            filters = (
                "scale=1080:1280:force_original_aspect_ratio=decrease,"
                "pad=1080:1440:(ow-iw)/2:0:color=0xfbfcff,setsar=1,"
                "drawbox=x=0:y=1280:w=iw:h=160:color=0x172033:t=fill,"
                f"drawtext={font_option}textfile='{caption_file}':fontcolor=white:fontsize=30:line_spacing=10:x=(w-text_w)/2:y=1320"
            )
            subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(screenshot), "-vf", filters, "-t", "10", "-r", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", str(clip)], check=True)
            clips.append(clip)
            subtitles.append(f"{index + 1}\n{timestamp(index * 10)} --> {timestamp((index + 1) * 10)}\n{caption}\n")
        concat = work / "clips.txt"
        concat.write_text("".join(f"file '{clip}'\n" for clip in clips), encoding="utf-8")
        output = args.showcase_dir / "walkthrough.mp4"
        subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", "-metadata", "comment=Edited walkthrough of actual Robot Doctor browser captures; synthetic fault; not a real-time benchmark.", str(output)], check=True)
        (args.showcase_dir / "walkthrough.srt").write_text("\n".join(subtitles), encoding="utf-8")
    print(f"Walkthrough generated: {output} (70 seconds, captions included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
