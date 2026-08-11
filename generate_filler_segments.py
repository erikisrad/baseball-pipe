"""
Generates a library of 1-second ad-break filler .ts segments, one per second
of remaining time, so a live ad break can be spliced with a countdown instead
of showing the real ad or stalling. Re-run whenever the design changes or a
new rendition needs its own filler set.
"""

import os
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(REPO_ROOT, "src", "baseball_pipe", "assets", "filler")
TMP_PNG = os.path.join(REPO_ROOT, "test_files", "_filler_frame_tmp.png")

MAX_SECONDS = 150  # observed ad breaks run ~120s; pad for safety

RENDITIONS = {
    "2500K": {"size": (960, 540), "fps": "30000/1001"},
}

def make_filler_frame(seconds_remaining, size):
    W, H = size
    img = Image.new("RGB", (W, H), (18, 24, 38))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, H // 2 - 4, W, H // 2 + 4], fill=(200, 30, 30))

    big_size = max(20, H // 15)
    small_size = max(14, H // 22)
    try:
        font_big = ImageFont.truetype("arial.ttf", big_size)
        font_small = ImageFont.truetype("arial.ttf", small_size)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    def centered_text(y, text, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        draw.text((W // 2 - w // 2, y), text, font=font, fill=fill)

    mins, secs = divmod(max(0, int(seconds_remaining)), 60)
    countdown = f"{mins}:{secs:02d}"

    centered_text(H // 2 - int(H * 0.093), "COMMERCIAL BREAK", font_big, (245, 245, 245))
    centered_text(H // 2 + int(H * 0.028), f"Resuming in {countdown}", font_small, (170, 175, 190))

    return img

def encode_ts(png_path, ts_path, size, fps, ts_offset):
    w, h = size
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", png_path,
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", "1",
        "-vf", f"scale={w}:{h}",
        "-r", fps,
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-mpegts_flags", "+resend_headers",
        "-output_ts_offset", f"{ts_offset:.6f}",
        "-f", "mpegts", ts_path,
    ], check=True, capture_output=True)

def probe_duration(ts_path):
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", ts_path,
    ], check=True, capture_output=True, text=True)
    return float(result.stdout.strip())

def generate_rendition(name, size, fps):
    start = time.perf_counter()
    rendition_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(rendition_dir, exist_ok=True)

    cumulative_offset = 0.0
    # generated in playback order (highest remaining time first, counting down to 0),
    # since that's the order segments actually get spliced into a live ad break --
    # timestamps must continue in that order, not by filename/index
    for seconds_remaining in range(MAX_SECONDS, -1, -1):
        frame = make_filler_frame(seconds_remaining, size)
        frame.save(TMP_PNG)

        ts_path = os.path.join(rendition_dir, f"filler_{seconds_remaining:03d}.ts")
        encode_ts(TMP_PNG, ts_path, size, fps, cumulative_offset)

        # drive the next segment's offset from this segment's *actual* measured
        # duration, not a fixed nominal value, so drift never accumulates
        cumulative_offset += probe_duration(ts_path)

    os.remove(TMP_PNG)
    elapsed = time.perf_counter() - start
    print(f"generated {MAX_SECONDS + 1} filler segments for {name} in {rendition_dir} ({elapsed:.1f}s)")

if __name__ == "__main__":
    for name, spec in RENDITIONS.items():
        generate_rendition(name, spec["size"], spec["fps"])
