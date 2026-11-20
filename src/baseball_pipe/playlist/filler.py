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
TMP_VIDEO_TS = os.path.join(tempfile.gettempdir(), "_filler_video_tmp.ts")

MAX_SECONDS = 248
OVERFLOW_SECONDS = 121
TAG_TEXT = "BaseballPipe, by Erik R."
AUDIO_SAMPLE_RATE = 48000
SEC_DURATION = 4
FILLER_BITRATE_HEADROOM = 0.8

GOP_SECONDS = 2

PMT_PID = 0x1e0
VIDEO_PID = 0x1e1
AUDIO_PID = 0x1e2

H264_PROFILE_NAMES = {
    66: "baseline",
    77: "main",
    100: "high",
    110: "high10",
    122: "high422",
    244: "high444",
}

def make_filler_frame(seconds_remaining, size):
    W, H = size
    img = Image.new("RGB", (W, H), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, H // 2 - 4, W, H // 2 + 4], fill=CROSSBAR_COLOR)

    big_size = max(20, H // 15)
    small_size = max(14, H // 22)
    tag_size = max(10, H // 35)
    try:
        font_big = ImageFont.truetype(FONT_PATH, big_size)
        font_small = ImageFont.truetype(FONT_PATH, small_size)
        font_tag = ImageFont.truetype(FONT_PATH, tag_size)
    except Exception:
        logger.warning("falling back to default font for filler segment")
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_tag = ImageFont.load_default()

    def centered_text(y, text, font, fill, stroke_width=0, stroke_fill=None):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)

        w = bbox[2] - bbox[0]

        draw.text((W // 2 - w // 2, y), text, font=font, fill=fill,
                   stroke_width=stroke_width, stroke_fill=stroke_fill)

    def bottom_right_text(text, font, fill, stroke_width=0, stroke_fill=None, margin=10):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text((W - margin - w, H - margin - h), text, font=font, fill=fill,
                   stroke_width=stroke_width, stroke_fill=stroke_fill)

    try:
        seconds_remaining = int(seconds_remaining)
        if seconds_remaining < 0:
            countdown = ""
        else:
            mins, secs = divmod(max(0, int(seconds_remaining)), 60)
            countdown = f"{mins}:{secs:02d}"
    except Exception:
        countdown = seconds_remaining
        
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


def video_segment_seconds(fps, sec_duration=SEC_DURATION):
    fps_value = Fraction(fps)
    return Fraction(round(float(fps_value)) * sec_duration) / fps_value

AUDIO_PACKET_SAMPLES = 1024
AUDIO_REFERENCE_FPS = "30000/1001"  # segments are 4.004s at 29.97 and 59.94fps

def audio_packet_count(countdown_start_number, fps, sec_duration=SEC_DURATION):
    k = (MAX_SECONDS - countdown_start_number) // sec_duration
    per_segment = video_segment_seconds(fps, sec_duration) * AUDIO_SAMPLE_RATE / AUDIO_PACKET_SAMPLES
    return round((k + 1) * per_segment) - round(k * per_segment)


def ensure_silent_audio_track(packet_count):
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
    video_frames = round(fps_value)  # ~1 second worth of frames at this fps
    bufsize_bps = bitrate_bps * 2
    gop_size = round(fps_value * GOP_SECONDS)
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
            "-x264-params",
            f"nal-hrd=cbr:force-cfr=1:bframes=2:b-adapt=1:"
            f"keyint={gop_size}:min-keyint={gop_size}:scenecut=0",
            "-f", "mpegts", TMP_VIDEO_TS,
        ], check=True, capture_output=True)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", TMP_VIDEO_TS,
            "-i", silent_audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c", "copy",
            "-mpegts_flags", "+resend_headers",
            "-mpegts_pmt_start_pid", str(PMT_PID),
            "-streamid", f"0:{VIDEO_PID}",
            "-streamid", f"1:{AUDIO_PID}",
            "-muxdelay", "0", "-muxpreload", "0",
            "-output_ts_offset", f"{ts_offset:.6f}",
            "-f", "mpegts", ts_path,
        ], check=True, capture_output=True)

def rendition_dir(size, fps):
    rendition_rel_dir = rendition_str(size, fps)
    return os.path.join(OUTPUT_DIR, rendition_rel_dir)

def rendition_str(size, fps):
    w, h = size
    fps_value = float(Fraction(fps))

    return f"{w}x{h}/{fps_value:.2f}fps"

def probe_start_timestamp(ts_path):
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "packet=pts_time",
        "-read_intervals", "%+#1",
        "-of", "csv=p=0",
        ts_path,
    ], check=True, capture_output=True, text=True)
    return float(result.stdout.strip().split(",")[0])

def probe_end_timestamp(ts_path):
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

ID3_TIMESTAMP_OWNER = b"com.apple.streaming.transportStreamTimestamp\x00"

def read_audio_id3_timestamp(data):
    if data[:3] != b"ID3":
        return None
    size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
    tag = data[10:10 + size]
    i = tag.find(ID3_TIMESTAMP_OWNER)
    if i < 0:
        return None
    start = i + len(ID3_TIMESTAMP_OWNER)
    return int.from_bytes(tag[start:start + 8], "big") / 90000

def probe_audio_end_timestamp(ts_path):
    with open(ts_path, "rb") as f:
        start = read_audio_id3_timestamp(f.read())
    if start is None:
        raise ValueError(f"no transport-stream timestamp in ID3 tag: {ts_path}")
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "packet=pts_time,duration_time",
        "-of", "csv=p=0",
        ts_path,
    ], check=True, capture_output=True, text=True)
    ends = []
    for line in result.stdout.splitlines():
        parts = [p for p in line.rstrip(",").split(",") if p]
        if len(parts) >= 2:
            ends.append(float(parts[0]) + float(parts[1]))
    return start + max(ends)

def rendition_segment_duration(fps, sec_duration=SEC_DURATION):
    return float(video_segment_seconds(fps, sec_duration))

def countdown_values(sec_duration=SEC_DURATION):
    return range(MAX_SECONDS, -OVERFLOW_SECONDS, -sec_duration)

def rendition_exists(size, fps, sec_duration=SEC_DURATION):
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
    avc_part = next(part for part in codecs.split(",") if part.strip().startswith("avc1."))
    hex_digits = avc_part.strip().split(".", 1)[1]
    profile_idc = int(hex_digits[0:2], 16)
    level_idc = int(hex_digits[4:6], 16)

    profile_name = H264_PROFILE_NAMES.get(profile_idc)
    if profile_name is None:
        raise ValueError(f"unrecognized H.264 profile_idc {profile_idc} in codecs {codecs!r}")

    return profile_name, level_idc

def generate_rendition(size, fps, bitrate_bps, profile, level_idc, sec_duration=SEC_DURATION):
    start = time.perf_counter()
    output_dir = rendition_dir(size, fps)
    os.makedirs(output_dir, exist_ok=True)
    generated_count = 0

    cumulative_offset = 0.0
    logger.info(f"generating filler segments for {size[0]}x{size[1]} @ {fps}fps into {output_dir} "
                f"({bitrate_bps}bps, profile={profile}, level={level_idc})")
    for countdown_start in countdown_values(sec_duration):
        ts_path = os.path.join(output_dir, f"filler_{countdown_start:03d}.ts")

        encode_ts(countdown_start, ts_path, size, fps, cumulative_offset, bitrate_bps, profile, level_idc, sec_duration)
        generated_count += 1

        cumulative_offset += rendition_segment_duration(fps, sec_duration)

    if os.path.exists(TMP_VIDEO_TS):
        os.remove(TMP_VIDEO_TS)

    duration = rendition_segment_duration(fps, sec_duration)
    with open(os.path.join(output_dir, "EXTINF"), "w") as f:
        f.write(f"{duration:.6f}")

    elapsed = time.perf_counter() - start
    logger.info(f"generated {generated_count} {duration:.6f}s segments into {output_dir} in {elapsed:.1f}s")

def ensure_rendition(size, fps, average_bandwidth, codecs, sec_duration=SEC_DURATION):
    if rendition_exists(size, fps, sec_duration):
        logger.debug(f"filler segments in {rendition_dir(size, fps)} already exist, skipping")
    else:
        bitrate_bps = int(average_bandwidth * FILLER_BITRATE_HEADROOM)
        profile, level_idc = parse_h264_profile_level(codecs)
        generate_rendition(size, fps, bitrate_bps, profile, level_idc, sec_duration)

    extinf_path = os.path.join(rendition_dir(size, fps), "EXTINF")
    with open(extinf_path) as f:
        return float(f.read().strip())


def ensure_silent_audio():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    counts = {audio_packet_count(c, AUDIO_REFERENCE_FPS) for c in countdown_values()}
    return {n: ensure_silent_audio_track(n) for n in sorted(counts)}

def ntsc_fraction_str(fps_decimal, tolerance=0.001):
    numerator_thousands = round(fps_decimal * 1001 / 1000)
    exact = Fraction(numerator_thousands * 1000, 1001)

    if abs(float(exact) - fps_decimal) > tolerance:
        return str(fps_decimal)

    return f"{exact.numerator}/{exact.denominator}"