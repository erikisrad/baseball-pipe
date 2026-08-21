"""
Generates a library of 1-second ad-break filler .ts segments, one per second
of remaining time, so a live ad break can be spliced with a countdown instead
of showing the real ad or stalling. Re-run whenever the design changes or a
new rendition needs its own filler set.
"""

import os
import subprocess
import tempfile
import time
from fractions import Fraction
from PIL import Image, ImageDraw, ImageFont
import logging

logger = logging.getLogger(__name__)

BACKGROUND_COLOR = (18, 24, 38) #dark blue
CROSSBAR_COLOR = (200, 30, 30) #red

# .../src/baseball_pipe -- the package directory, not the repo root. Derived
# relative to this file (two levels up from src/baseball_pipe/playlist/)
# rather than assumed, so this stays correct if the script moves again
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# where the final, per-rendition filler segments get written -- this is
# inside the actual package so the running server can find them at runtime
OUTPUT_DIR = os.path.join(PACKAGE_DIR, "assets", "filler")

# scratch PNG reused for every frame we render -- overwritten each iteration
# rather than creating hundreds of throwaway image files. Lives in the OS
# temp dir since it's disposable and has no reason to live in the repo
TMP_PNG = os.path.join(tempfile.gettempdir(), "_filler_frame_tmp.png")

MAX_SECONDS = 150  # observed ad breaks run ~120s; pad for safety

def ntsc_fraction_str(fps_decimal, tolerance=0.001):
    """Recover the exact NTSC rational rate (e.g. "30000/1001") from a rounded decimal fps.

    HLS master playlists report FRAME-RATE as a decimal rounded to 3 places
    (e.g. 29.97), but NTSC-family rates are actually X*1000/1001 exactly
    (29.970029970...). ffmpeg needs the exact fraction, not the rounded
    decimal, to match a real broadcast segment's timing precisely -- feeding
    it the rounded decimal instead reintroduces the drift we specifically
    fixed earlier with -output_ts_offset.

    Falls back to the original decimal (as a string) if it doesn't look like
    an NTSC-family rate, rather than silently corrupting a genuinely
    different frame rate (e.g. an exact 25.000 PAL rate).
    """
    numerator_thousands = round(fps_decimal * 1001 / 1000)
    exact = Fraction(numerator_thousands * 1000, 1001)

    if abs(float(exact) - fps_decimal) > tolerance:
        return str(fps_decimal)

    return f"{exact.numerator}/{exact.denominator}"

def rendition_dir(size, fps):
    """Build the assets/filler/<resolution>/<framerate>/ directory for a rendition."""
    w, h = size
    # fps arrives as a fraction string (e.g. "30000/1001") so the exact NTSC
    # rate survives -- Fraction parses that natively, then we round to a
    # human-readable decimal purely for the folder name
    fps_value = float(Fraction(fps))
    return os.path.join(OUTPUT_DIR, f"{w}x{h}", f"{fps_value:.2f}fps")

def rendition_exists(size, fps, max_length=MAX_SECONDS):
    """Check whether a rendition's filler segments are on disk up to max_length seconds.

    A rendition folder existing isn't enough -- generation can be interrupted
    partway through (generate_rendition() already supports resuming), so this
    only reports True if every filler_NNN.ts from 0 up to max_length is present.
    Also ensures the EXTINF duration file sits alongside them, generating it
    now if an earlier run never got that far.
    """
    dir_path = rendition_dir(size, fps)
    if not os.path.isdir(dir_path):
        return False

    segments_exist = all(
        os.path.exists(os.path.join(dir_path, f"filler_{seconds_remaining:03d}.ts"))
        for seconds_remaining in range(max_length + 1)
    )
    if not segments_exist:
        return False

    extinf_path = os.path.join(dir_path, "EXTINF")
    if not os.path.exists(extinf_path):
        duration = rendition_segment_duration(size, fps)
        with open(extinf_path, "w") as f:
            f.write(f"{duration:.6f}")

    return True

def make_filler_frame(seconds_remaining, size):
    """Render a single countdown frame as a PIL Image (not yet encoded to video)."""
    W, H = size
    # dark background card
    img = Image.new("RGB", (W, H), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    # red accent bar through the middle, purely decorative
    draw.rectangle([0, H // 2 - 4, W, H // 2 + 4], fill=CROSSBAR_COLOR)

    # scale font sizes off frame height so smaller renditions still read fine
    big_size = max(20, H // 15)
    small_size = max(14, H // 22)
    try:
        font_big = ImageFont.truetype("arial.ttf", big_size)
        font_small = ImageFont.truetype("arial.ttf", small_size)
    except Exception:
        # arial.ttf may not be resolvable on every machine/OS -- fall back
        # to PIL's built-in bitmap font rather than crashing generation
        logger.warning("falling back to default font for filler segment")
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    def centered_text(y, text, font, fill, stroke_width=0, stroke_fill=None):
        # textbbox measures the pixel box the text would occupy if drawn at
        # (0, 0) -- it doesn't draw anything, it just tells us how big the
        # rendered string would be for this exact font/text/stroke combo.
        # we pass the *same* stroke_width here as we'll use in the real
        # draw.text() call below, because the outline adds extra pixels
        # around every glyph -- measuring without it would give a narrower
        # width than what actually gets painted, and the centering math
        # would be off by however thick the outline is.
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)

        # bbox is a 4-tuple (left, top, right, bottom); right - left is the
        # rendered width in pixels. we don't need the vertical extent here
        # since y is passed in directly rather than being centered.
        w = bbox[2] - bbox[0]

        # shift the x position left by half the text's width so the text's
        # midpoint lands on the frame's horizontal midpoint (W // 2),
        # producing the same "centered" look regardless of how long the
        # string is (e.g. "0:05" vs "COMMERCIAL BREAK")
        draw.text((W // 2 - w // 2, y), text, font=font, fill=fill,
                   stroke_width=stroke_width, stroke_fill=stroke_fill)

    # split total seconds into minutes/seconds for a "M:SS" style readout
    # (max(0, ...) guards against a negative countdown if this ever gets
    # called with a value past the ad break's actual end)
    mins, secs = divmod(max(0, int(seconds_remaining)), 60)
    countdown = f"{mins}:{secs:02d}"

    # black outline so the text stays readable over the background/crossbar
    big_stroke = max(1, big_size // 12)
    small_stroke = max(1, small_size // 12)

    centered_text(H // 2 - int(H * 0.093), "COMMERCIAL BREAK", font_big, (245, 245, 245),
                  stroke_width=big_stroke, stroke_fill=(0, 0, 0))
    centered_text(H // 2 + int(H * 0.028), countdown, font_small, (170, 175, 190),
                  stroke_width=small_stroke, stroke_fill=(0, 0, 0))

    return img

def encode_ts(png_path, ts_path, size, fps, ts_offset):
    """Encode a single still PNG into a 1-second MPEG-TS segment matching real stream specs."""
    w, h = size
    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", png_path,          # loop the still image as video input
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",  # silent stereo audio track
        "-t", "1",                              # exactly 1 second of output
        "-vf", f"scale={w}:{h}",
        "-r", str(fps),
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        # resend PAT/PMT at the start of this segment so a player tuning
        # into just this file (as HLS players do) can still decode it
        "-mpegts_flags", "+resend_headers",
        # shift this segment's own PTS/DTS to start where the previous
        # segment left off, so consecutive filler segments share one
        # continuous timeline instead of each resetting to ~0
        "-output_ts_offset", f"{ts_offset:.6f}",
        "-f", "mpegts", ts_path,
    ], check=True, capture_output=True)

def probe_duration(ts_path):
    """Read back the *actual* encoded duration of a segment (not the nominal 1s we asked for)."""
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", ts_path,
    ], check=True, capture_output=True, text=True)
    return float(result.stdout.strip())

def rendition_segment_duration(size, fps):
    """Probe the real encoded duration of a rendition's filler segments via segment 1.

    All 151 segments in a rendition share the same encode settings, so
    segment 1's measured duration stands in for the whole set. Callers need
    this instead of assuming a fixed nominal duration like 1.001s -- AAC's
    1024-sample frame boundaries push the real container duration slightly
    past the video track's own length (see probe_duration()).
    """
    ts_path = os.path.join(rendition_dir(size, fps), "filler_001.ts")
    return probe_duration(ts_path)

def verify_segment_durations():
    """Walk every generated filler segment on disk and confirm they all share one duration.

    rendition_segment_duration() assumes one probed file (segment 1) speaks
    for every segment in every rendition -- this actually checks that
    assumption across the whole assets/filler/ tree instead of just trusting
    it. Returns the shared duration if every segment agrees (within a small
    tolerance for floating-point noise); raises otherwise.
    """
    durations = {}  # ts_path -> measured duration, kept around for the error message

    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for filename in files:
            if not filename.endswith(".ts"):
                continue
            ts_path = os.path.join(root, filename)
            durations[ts_path] = probe_duration(ts_path)

    if not durations:
        raise ValueError(f"no filler segments found under {OUTPUT_DIR}")

    reference = next(iter(durations.values()))
    # AAC's 1024-sample frame quantization is the smallest genuine difference
    # we'd ever expect (~21ms) -- anything within 1ms is floating-point noise,
    # not a real inconsistency
    inconsistent = {path: d for path, d in durations.items() if abs(d - reference) > 0.001}

    if inconsistent:
        raise ValueError(
            f"inconsistent filler segment durations found (expected {reference}s): {inconsistent}"
        )

    return reference

def generate_rendition(size, fps):
    """Generate (or resume generating) the full filler segment set for one rendition."""
    start = time.perf_counter()
    output_dir = rendition_dir(size, fps)
    os.makedirs(output_dir, exist_ok=True)
    generated_count = 0

    cumulative_offset = 0.0
    # generated in playback order (highest remaining time first, counting down to 0),
    # since that's the order segments actually get spliced into a live ad break --
    # timestamps must continue in that order, not by filename/index
    logger.info(f"generating filler segments for {size[0]}x{size[1]} @ {fps}fps into {output_dir}")
    for seconds_remaining in range(MAX_SECONDS, -1, -1):
        ts_path = os.path.join(output_dir, f"filler_{seconds_remaining:03d}.ts")

        # if os.path.exists(ts_path):
        #     # already generated on a previous run -- don't waste time re-encoding it,
        #     # but we still need its real duration to keep cumulative_offset accurate
        #     # for whichever segment comes next
        #     cumulative_offset += probe_duration(ts_path)
        #     skipped_count += 1
        #     continue

        frame = make_filler_frame(seconds_remaining, size)
        frame.save(TMP_PNG)

        encode_ts(TMP_PNG, ts_path, size, fps, cumulative_offset)
        generated_count += 1

        # drive the next segment's offset from this segment's *actual* measured
        # duration, not a fixed nominal value, so drift never accumulates
        cumulative_offset += probe_duration(ts_path)

    if os.path.exists(TMP_PNG):
        os.remove(TMP_PNG)

    # record the segments' real encoded duration once, so callers (e.g. the
    # live playlist rewriter) can read it directly instead of shelling out
    # to ffprobe on every request
    duration = rendition_segment_duration(size, fps)
    with open(os.path.join(output_dir, "EXTINF"), "w") as f:
        f.write(f"{duration:.6f}")

    elapsed = time.perf_counter() - start
    logger.info(f"generated {generated_count} {duration:.6f}s segments into {output_dir} in {elapsed:.1f}s")

def ensure_rendition(size, fps):
    """Make sure a rendition's full filler segment set is on disk, generating it if not.

    Returns the segments' real duration, read from the EXTINF file that
    either path (already-existing or freshly-generated) leaves behind --
    callers get a build-and-fetch in one call instead of a separate probe.
    """
    if rendition_exists(size, fps):
        logger.debug(f"filler segments in {rendition_dir(size, fps)} already exist, skipping")
    else:
        generate_rendition(size, fps)

    extinf_path = os.path.join(rendition_dir(size, fps), "EXTINF")
    with open(extinf_path) as f:
        return float(f.read().strip())


