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

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PACKAGE_DIR, "assets", "filler")
FONT_PATH = os.path.join(PACKAGE_DIR, "assets", "fonts", "DejaVuSans.ttf")
TMP_PNG = os.path.join(tempfile.gettempdir(), "_filler_frame_tmp.png")
TMP_VIDEO_TS = os.path.join(tempfile.gettempdir(), "_filler_video_tmp.ts")
SILENT_AUDIO_PATH = os.path.join(OUTPUT_DIR, "silent_audio.aac")

MAX_SECONDS = 248
OVERFLOW_SECONDS = 121
TAG_TEXT = "BaseballPipe, By Erik R"
AUDIO_SAMPLE_RATE = 48000
SEC_DURATION = 4
FILLER_BITRATE_HEADROOM = 0.8

H264_PROFILE_NAMES = {
    66: "baseline",
    77: "main",
    100: "high",
    110: "high10",
    122: "high422",
    244: "high444",
}

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
    tag_size = max(10, H // 35)
    try:
        font_big = ImageFont.truetype(FONT_PATH, big_size)
        font_small = ImageFont.truetype(FONT_PATH, small_size)
        font_tag = ImageFont.truetype(FONT_PATH, tag_size)
    except Exception:
        # only reachable if the bundled font file itself is somehow missing
        # or corrupted -- fall back to PIL's built-in bitmap font rather
        # than crashing generation entirely
        logger.warning("falling back to default font for filler segment")
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_tag = ImageFont.load_default()

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

    def bottom_right_text(text, font, fill, stroke_width=0, stroke_fill=None, margin=10):
        # same idea as centered_text's width measurement, but anchored to
        # the frame's bottom-right corner instead of horizontally centered
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text((W - margin - w, H - margin - h), text, font=font, fill=fill,
                   stroke_width=stroke_width, stroke_fill=stroke_fill)

    # split total seconds into minutes/seconds for a "M:SS" style readout
    # (max(0, ...) guards against a negative countdown if this ever gets
    # called with a value past the ad break's actual end)
    try:
        seconds_remaining = int(seconds_remaining)
        if seconds_remaining < 0:
            countdown = ""
        else:
            mins, secs = divmod(max(0, int(seconds_remaining)), 60)
            countdown = f"{mins}:{secs:02d}"
    except Exception:
        countdown = seconds_remaining
        
    # black outline so the text stays readable over the background/crossbar
    big_stroke = max(1, big_size // 12)
    small_stroke = max(1, small_size // 12)
    tag_stroke = max(1, tag_size // 12)

    centered_text(H // 2 - int(H * 0.093), "COMMERCIAL BREAK", font_big, (245, 245, 245),
                  stroke_width=big_stroke, stroke_fill=(0, 0, 0))
    centered_text(H // 2 + int(H * 0.028), countdown, font_small, (170, 175, 190),
                  stroke_width=small_stroke, stroke_fill=(0, 0, 0))
    bottom_right_text(TAG_TEXT, font_tag, (170, 175, 190),
                       stroke_width=tag_stroke, stroke_fill=(0, 0, 0))

    return img


AUDIO_PACKET_SAMPLES = 1024


def video_segment_seconds(fps, sec_duration=SEC_DURATION):
    fps_value = Fraction(fps)
    return Fraction(round(float(fps_value)) * sec_duration) / fps_value


def audio_packet_count(countdown_start_number, fps, sec_duration=SEC_DURATION):
    k = (MAX_SECONDS - countdown_start_number) // sec_duration
    per_segment = video_segment_seconds(fps, sec_duration) * AUDIO_SAMPLE_RATE / AUDIO_PACKET_SAMPLES
    return round((k + 1) * per_segment) - round(k * per_segment)


def ensure_silent_audio_track(packet_count):
    """Generate (once per packet count) and cache a silent AAC track of exactly
    packet_count packets. Requests one fewer frame than the target -- AAC's
    one-frame encoder priming delay surfaces as an extra output packet when
    audio has no video stream to implicitly bound it.
    """
    audio_path = os.path.join(OUTPUT_DIR, f"silent_audio_{packet_count}pk.aac")
    if os.path.exists(audio_path):
        return audio_path

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
        "-frames:a", str(packet_count - 1),
        "-c:a", "aac", "-b:a", "128k",
        "-f", "adts", audio_path,
    ], check=True, capture_output=True)
    return audio_path


def encode_ts(countdown_start_number, ts_path, size, fps, ts_offset, bitrate_bps, profile, level_idc, sec_duration=SEC_DURATION):

    w, h = size
    fps_value = float(Fraction(fps))
    video_frames = round(fps_value)  # ~1 second's worth of frames at this fps
    bufsize_bps = bitrate_bps * 2
    silent_audio_path = ensure_silent_audio_track(audio_packet_count(countdown_start_number, fps, sec_duration))

    with tempfile.TemporaryDirectory() as tmp_dir:
        frame_index = 0
        for i in range(sec_duration):
            frame = make_filler_frame(countdown_start_number - i, size)
            for _ in range(video_frames):
                frame.save(os.path.join(tmp_dir, f"frame_{frame_index:06d}.png"))
                frame_index += 1

        subprocess.run([
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", os.path.join(tmp_dir, "frame_%06d.png"),
            "-frames:v", str(frame_index),
            "-vf", f"scale={w}:{h}",
            "-r", str(fps),
            "-c:v", "libx264", "-profile:v", profile, "-level:v", str(level_idc), "-pix_fmt", "yuv420p",
            "-b:v", str(bitrate_bps), "-minrate", str(bitrate_bps), "-maxrate", str(bitrate_bps),
            "-bufsize", str(bufsize_bps),
            "-x264-params", "nal-hrd=cbr:force-cfr=1:bframes=0",
            "-f", "mpegts", TMP_VIDEO_TS,
        ], check=True, capture_output=True)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", TMP_VIDEO_TS,
            "-i", silent_audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c", "copy",
            "-mpegts_flags", "+resend_headers",
            "-muxdelay", "0", "-muxpreload", "0",
            "-output_ts_offset", f"{ts_offset:.6f}",
            "-f", "mpegts", ts_path,
        ], check=True, capture_output=True)

def rendition_dir(size, fps):
    """Build the assets/filler/<resolution>/<framerate>/ directory for a rendition."""
    # fps arrives as a fraction string (e.g. "30000/1001") so the exact NTSC
    # rate survives -- Fraction parses that natively, then we round to a
    # human-readable decimal purely for the folder name))

    rendition_rel_dir = rendition_str(size, fps)
    return os.path.join(OUTPUT_DIR, rendition_rel_dir)

def rendition_str(size, fps):
    # a URL fragment, not a filesystem path -- always forward-slash
    # regardless of OS (os.path.join here would use os.sep, producing a
    # literal backslash on Windows that breaks the URL it gets embedded in)
    w, h = size
    fps_value = float(Fraction(fps))

    return f"{w}x{h}/{fps_value:.2f}fps"

def probe_start_timestamp(ts_path):
    """Read a segment's actual embedded start PTS (seconds), from its audio track.

    Audio, not video, is the sync reference here -- a splice anchored to
    video leaves audio wherever real video's PTS happened to be, and a
    listener notices audio timing errors far more readily than a few
    milliseconds of video jitter, so any residual splice-point slop should
    land on video instead.

    Reads only the first audio packet via -read_intervals rather than the
    whole file -- this is what actually varies per segment (unlike duration,
    which is uniform across a rendition), e.g. as an anchor for splicing
    filler segments so their timestamps continue from wherever a real
    segment's own embedded PTS actually is, since that can diverge from what
    EXTINF declares. ts_path can be a local file path or an http(s) URL --
    ffprobe reads both directly, so this also works on a real upstream
    segment without downloading it first.
    """
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "packet=pts_time",
        "-read_intervals", "%+#1",
        "-of", "csv=p=0",
        ts_path,
    ], check=True, capture_output=True, text=True)
    # csv=p=0 still leaves a trailing comma here -- take just the value
    return float(result.stdout.strip().split(",")[0])

def probe_end_timestamp(ts_path):
    """Read a segment's real video end (seconds): the last frame's PTS plus its
    duration. Splices anchor here, since video is the timing reference and real
    segments' video ends exactly where the next one begins.
    """
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,duration_time",
        "-of", "csv=p=0",
        ts_path,
    ], check=True, capture_output=True, text=True)
    ends = []
    for line in result.stdout.splitlines():
        parts = [p for p in line.rstrip(",").split(",") if p]
        if len(parts) >= 2:
            ends.append(float(parts[0]) + float(parts[1]))
    return max(ends)

def rendition_segment_duration(fps, sec_duration=SEC_DURATION):
    """The duration every filler segment declares (EXTINF, tsoffset stepping):
    its video length, frames / fps. Real segments declare their video length
    too, with their audio packets alternating around it (see audio_packet_count()).
    """
    return float(video_segment_seconds(fps, sec_duration))

def countdown_values(sec_duration=SEC_DURATION):
    """Countdown numbers each filler segment starts on, from MAX_SECONDS down to the overflow margin."""
    return range(MAX_SECONDS, -OVERFLOW_SECONDS, -sec_duration)

def rendition_exists(size, fps, sec_duration=SEC_DURATION):
    """Check whether a rendition's filler segments are all on disk.

    A rendition folder existing isn't enough -- generation can be interrupted
    partway through (generate_rendition() already supports resuming), so this
    only reports True if every filler_NNN.ts in countdown_values() is present.
    Also ensures the EXTINF duration file sits alongside them, generating it
    now if an earlier run never got that far.
    """
    dir_path = rendition_dir(size, fps)
    if not os.path.isdir(dir_path):
        return False

    segments_exist = all(
        os.path.exists(os.path.join(dir_path, f"filler_{countdown_start:03d}.ts"))
        for countdown_start in countdown_values(sec_duration)
    )
    if not segments_exist:
        return False

    extinf_path = os.path.join(dir_path, "EXTINF")
    if not os.path.exists(extinf_path):
        duration = rendition_segment_duration(fps, sec_duration)
        with open(extinf_path, "w") as f:
            f.write(f"{duration:.6f}")

    return True

def parse_h264_profile_level(codecs):
    """Extract H.264 profile name and level from an HLS CODECS attribute.

    e.g. "avc1.640029,mp4a.40.2" -> ("high", 41). Matching filler segments'
    encoded profile/level to real content's own (rather than letting ffmpeg
    auto-select a level, or hardcoding one profile for every resolution) is
    what avoids the mid-stream decoder reconfiguration described above --
    see FILLER_BITRATE_HEADROOM's comment for the full chain.
    """
    avc_part = next(part for part in codecs.split(",") if part.strip().startswith("avc1."))
    hex_digits = avc_part.strip().split(".", 1)[1]
    profile_idc = int(hex_digits[0:2], 16)
    level_idc = int(hex_digits[4:6], 16)

    profile_name = H264_PROFILE_NAMES.get(profile_idc)
    if profile_name is None:
        raise ValueError(f"unrecognized H.264 profile_idc {profile_idc} in codecs {codecs!r}")

    return profile_name, level_idc

def generate_rendition(size, fps, bitrate_bps, profile, level_idc, sec_duration=SEC_DURATION):
    """Generate (or resume generating) the full filler segment set for one rendition."""
    start = time.perf_counter()
    output_dir = rendition_dir(size, fps)
    os.makedirs(output_dir, exist_ok=True)
    generated_count = 0

    cumulative_offset = 0.0
    # generated in playback order (highest remaining time first, counting down to 0),
    # since that's the order segments actually get spliced into a live ad break --
    # timestamps must continue in that order, not by filename/index
    logger.info(f"generating filler segments for {size[0]}x{size[1]} @ {fps}fps into {output_dir} "
                f"({bitrate_bps}bps, profile={profile}, level={level_idc})")
    for countdown_start in countdown_values(sec_duration):
        ts_path = os.path.join(output_dir, f"filler_{countdown_start:03d}.ts")

        encode_ts(countdown_start, ts_path, size, fps, cumulative_offset, bitrate_bps, profile, level_idc, sec_duration)
        generated_count += 1

        # drive the next segment's offset from this segment's *actual* measured
        # duration, not a fixed nominal value, so drift never accumulates
        cumulative_offset += rendition_segment_duration(fps, sec_duration)

    if os.path.exists(TMP_VIDEO_TS):
        os.remove(TMP_VIDEO_TS)

    # record the segments' real encoded duration once, so callers (e.g. the
    # live playlist rewriter) can read it directly instead of shelling out
    # to ffprobe on every request
    duration = rendition_segment_duration(fps, sec_duration)
    with open(os.path.join(output_dir, "EXTINF"), "w") as f:
        f.write(f"{duration:.6f}")

    elapsed = time.perf_counter() - start
    logger.info(f"generated {generated_count} {duration:.6f}s segments into {output_dir} in {elapsed:.1f}s")

def ensure_rendition(size, fps, average_bandwidth, codecs, sec_duration=SEC_DURATION):
    """Make sure a rendition's full filler segment set is on disk, generating it if not.

    average_bandwidth (bps) and codecs (the raw HLS CODECS attribute string)
    come from the real rendition this filler set stands in for -- only used
    if generation is actually needed, since an already-existing rendition's
    encode settings are already baked into its files on disk. Assumes both
    values are stable for a given (size, fps) across different broadcasts,
    which held true across every master playlist checked this session.

    Returns the segments' real duration, read from the EXTINF file that
    either path (already-existing or freshly-generated) leaves behind --
    callers get a build-and-fetch in one call instead of a separate probe.
    """
    if rendition_exists(size, fps, sec_duration):
        logger.debug(f"filler segments in {rendition_dir(size, fps)} already exist, skipping")
    else:
        bitrate_bps = int(average_bandwidth * FILLER_BITRATE_HEADROOM)
        profile, level_idc = parse_h264_profile_level(codecs)
        generate_rendition(size, fps, bitrate_bps, profile, level_idc, sec_duration)

    extinf_path = os.path.join(rendition_dir(size, fps), "EXTINF")
    with open(extinf_path) as f:
        return float(f.read().strip())


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