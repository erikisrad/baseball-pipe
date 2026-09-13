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

# bundled font (DejaVu Sans, Bitstream Vera license -- see assets/fonts/LICENSE_DEJAVU)
# so text rendering doesn't depend on "arial.ttf" happening to be resolvable
# on whatever OS this runs on -- it isn't on a bare Linux server, which has
# no Arial at all, and PIL has no equivalent of Windows' font-name lookup
FONT_PATH = os.path.join(PACKAGE_DIR, "assets", "fonts", "DejaVuSans.ttf")

# scratch PNG reused for every frame we render -- overwritten each iteration
# rather than creating hundreds of throwaway image files. Lives in the OS
# temp dir since it's disposable and has no reason to live in the repo
TMP_PNG = os.path.join(tempfile.gettempdir(), "_filler_frame_tmp.png")

# scratch video-only intermediate, reused/overwritten the same way as TMP_PNG
# (see encode_ts())
TMP_VIDEO_TS = os.path.join(tempfile.gettempdir(), "_filler_video_tmp.ts")

# every filler segment across every rendition uses the exact same audio (see
# ensure_silent_audio_track()), so it's rendered once here and reused rather
# than living alongside per-rendition output under OUTPUT_DIR
SILENT_AUDIO_PATH = os.path.join(OUTPUT_DIR, "silent_audio.aac")

MAX_SECONDS = 150  # observed ad breaks run ~120s; pad for safety
TAG_TEXT = "BaseballPipe, By Erik R"

# CBR target is derived per-rendition from that rendition's own real
# AVERAGE-BANDWIDTH (see ensure_rendition()), not one flat constant applied
# everywhere. VHS estimates a segment's "bandwidth" as bytes/downloadTime; a
# plain CRF-encoded static image collapses to ~15KB, downloading in ~35ms
# almost entirely spent on TTFB, which produces a noisy implied bandwidth
# landing in the middle of a real rendition ladder instead of above it --
# causing VHS to switch renditions on nearly every filler segment, and each
# switch re-syncs to a point behind the already-buffered frontier, which is
# what actually triggers the permanent "excessive segment downloading"
# exclusion cascade (see the ad-break investigation history for the full
# chain). A flat, oversized bitrate applied to every rendition avoided that,
# but it also turned out to cause two other real problems: filler segments
# many times heavier than the real content they replace exhausted the
# browser's MSE SourceBuffer quota during long ad breaks, and the elevated
# bitrate pushed libx264's auto-selected H.264 level above what real content
# actually uses for that resolution -- forcing the decoder to reconfigure
# mid-stream at every ad break, which is what was causing audio to
# progressively drift ahead of video (confirmed via chrome://media-internals:
# a "video decoder config changed midstream" event immediately preceded a
# run of growing "Large timestamp gap" warnings). Deriving the target from
# each rendition's own real average bitrate (with headroom) keeps segments
# proportionate to real content instead.
#
# libx264's HRD filler-padding doesn't reach the nominal rate within a single
# ~1s/30-frame buffer-fill window -- empirically it lands at ~80% of the
# requested -b:v, so real margin (not just noise-avoidance margin) has to be
# requested to land close to the target.
FILLER_BITRATE_HEADROOM = 0.8

# HLS's CODECS attribute encodes H.264 profile/level directly (RFC 6381:
# "avc1.PPCCLL", hex profile_idc/constraints/level_idc) -- mapping used by
# parse_h264_profile_level() below to translate profile_idc into the name
# ffmpeg's -profile:v expects.
H264_PROFILE_NAMES = {
    66: "baseline",
    77: "main",
    100: "high",
    110: "high10",
    122: "high422",
    244: "high444",
}

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

# AAC encodes in fixed 1024-sample frames (@48kHz = 0.021333s/frame), which
# has nothing to do with video resolution or frame rate -- pinning audio to
# a FIXED frame count (rather than letting `-t 1` truncate it implicitly)
# guarantees every rendition's audio track has an identical real duration.
# 47 frames = ceil(48000/1024) = just over 1 second (~1.002667s).
AUDIO_FRAMES = 47
AUDIO_SAMPLE_RATE = 48000

def ensure_silent_audio_track():
    """Generate (once) and cache the silent AAC track shared by every filler segment.

    Every segment across every rendition uses the exact same audio --
    AUDIO_FRAMES of pure digital silence at AUDIO_SAMPLE_RATE -- so it only
    ever needs to be encoded once, ever, rather than re-encoded live for
    every one of the ~2400+ segments across every rendition.

    This isn't just an efficiency win: encoding it fresh, live, in the same
    ffmpeg process as each segment's own video (a real x264 encode, much
    heavier and slower per-frame than trivially encoding silence) confused
    ffmpeg 4.4.2's mpegts muxer badly enough to silently truncate audio well
    short of its requested frame count on the slowest/heaviest renditions --
    confirmed directly on the production server: asking for 47 frames
    produced only 19, with the muxer repeatedly logging "cur_dts is invalid"
    (a message ffmpeg itself documents as only expected once per stream).
    Pairing two *already-encoded* files in a stream-copy combine (see
    encode_ts()) sidesteps that mismatch entirely, since neither side is
    being live-encoded during that final mux.

    Requests AUDIO_FRAMES-1, not AUDIO_FRAMES, from the encoder -- confirmed
    directly (A/B tested on identical ffmpeg builds) that AAC's own
    documented one-frame encoder priming delay ("delay 1024" in ffmpeg's own
    stream metadata) shows up as a literal extra output packet when audio is
    encoded with no video stream present to implicitly bound its duration.
    In the old single-invocation encode, video's own explicit duration
    (-frames:v, ~1.001s) happened to sit just before that priming-inclusive
    48th packet's timestamp (~1.0027s), silently trimming it off and leaving
    exactly AUDIO_FRAMES real packets -- which is what every AUDIO_FRAMES-
    based comment/calculation elsewhere in this file assumes. Encoding audio
    alone removes that implicit bound, so this compensates for the same
    priming delay directly instead.
    """
    if os.path.exists(SILENT_AUDIO_PATH):
        return SILENT_AUDIO_PATH

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
        "-frames:a", str(AUDIO_FRAMES - 1),
        "-c:a", "aac", "-b:a", "128k",
        "-f", "adts", SILENT_AUDIO_PATH,
    ], check=True, capture_output=True)
    return SILENT_AUDIO_PATH

def encode_ts(png_path, ts_path, size, fps, ts_offset, bitrate_bps, profile, level_idc):
    """Encode a single still PNG into a ~1-second MPEG-TS segment matching real stream specs.

    Video is cut to an explicit frame count instead of a nominal `-t 1` --
    an implicit time-based cutoff rounds to a different real duration at
    different frame rates (this is what caused two renditions in the same
    ad break to end up with a different number of filler segments for the
    same real elapsed time: 960x540@29.97fps and 1280x720@59.94fps measured
    different real container durations for what was nominally "1 second").

    Encoded in two steps rather than one: video alone first, then combined
    with the cached silent audio track (see ensure_silent_audio_track()) via
    a fast stream-copy remux, instead of live-encoding both simultaneously
    into one muxer the way this used to work. That combine step is also
    where this segment's own PTS/DTS get shifted to its place in the
    rendition's running timeline, and where the mpegts-level settings
    (PCR/PSI/PAT-PMT behavior) get applied -- video's own encode doesn't
    need any of that since it's not final output yet.

    bitrate_bps, profile, and level_idc all come from the real rendition
    this filler is standing in for (see ensure_rendition()) -- not fixed
    constants -- so the encoded segment matches real content closely enough
    that VHS's bandwidth estimator and the browser's H.264 decoder both
    treat it the same way they'd treat the content it's replacing.
    """
    w, h = size
    fps_value = float(Fraction(fps))
    video_frames = round(fps_value)  # ~1 second's worth of frames at this fps
    bufsize_bps = bitrate_bps * 2
    silent_audio_path = ensure_silent_audio_track()

    subprocess.run([
        "ffmpeg", "-y",
        # -framerate here is an INPUT option -- it tells the image2/loop
        # demuxer to decode the looped still at exactly this rate. Without
        # it, that rate is otherwise unspecified and defaults vary by
        # ffmpeg build/version (commonly 25fps) -- the later output -r would
        # then be resampling from whatever that default was instead of
        # decoding at the target rate to begin with, which is inconsistent
        # across platforms/ffmpeg versions and is what caused video_frames
        # (computed assuming decode-at-target-fps) to yield a different real
        # duration on Linux than on Windows for the same command.
        "-framerate", str(fps),
        "-loop", "1", "-i", png_path,          # loop the still image as video input
        "-frames:v", str(video_frames),
        "-vf", f"scale={w}:{h}",
        "-r", str(fps),
        "-c:v", "libx264", "-profile:v", profile, "-level:v", str(level_idc), "-pix_fmt", "yuv420p",
        # force real CBR (not just a rate cap) so libx264 pads a static
        # frame's near-empty encode out to bitrate_bps with compliant
        # H.264 filler-data NAL units, rather than undershooting it the way
        # a plain -b:v ceiling would on genuinely static content
        "-b:v", str(bitrate_bps), "-minrate", str(bitrate_bps), "-maxrate", str(bitrate_bps),
        "-bufsize", str(bufsize_bps),
        # bframes=0 -- x264's default B-frame reordering doesn't flush
        # cleanly when abruptly cut off by -frames:v on a sequence this
        # short: real segments showed the last several encoded frames with
        # undefined PTS/DTS (confirmed via ffprobe), which meant the browser
        # only recognized a sliver of each filler segment as actually
        # buffered/playable -- appending the full 2MB+ file while the
        # *effective* buffered duration was a fraction of it, silently
        # fragmenting the buffer with gaps throughout every ad break. With
        # no B-frames, PTS==DTS for every frame and there's no reordering to
        # get cut off mid-flush. (rc-lookahead=0 was also forced alongside
        # this originally, but that combined with nal-hrd=cbr to make an
        # old libx264 build (Ubuntu 22.04's ffmpeg 4.4.2) hang indefinitely
        # on the highest-resolution/frame-count rendition -- lookahead
        # doesn't affect B-frame reordering, so dropping it here and letting
        # x264 use its default lookahead should avoid that without
        # reintroducing the PTS corruption bframes=0 fixes.)
        "-x264-params", "nal-hrd=cbr:force-cfr=1:bframes=0",
        "-f", "mpegts", TMP_VIDEO_TS,
    ], check=True, capture_output=True)

    # combine video with the cached silent audio -- both inputs are already
    # fully encoded, so this is a stream-copy remux, not two live encoders
    # racing each other (see ensure_silent_audio_track() for why that matters)
    subprocess.run([
        "ffmpeg", "-y",
        "-i", TMP_VIDEO_TS,
        "-i", silent_audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c", "copy",
        # resend PAT/PMT at the start of this segment so a player tuning
        # into just this file (as HLS players do) can still decode it
        "-mpegts_flags", "+resend_headers",
        # ffmpeg's mpegts muxer defaults to placing the initial PCR
        # ~0.7s *before* the first frame's own PTS (its "-muxdelay"
        # default) -- disabling both here keeps this segment's own PCR
        # aligned with its PTS/DTS instead of carrying that offset into
        # the file (matches the same flags already used when serve-time
        # rewrites a segment's timestamps in media_handler.py)
        "-muxdelay", "0", "-muxpreload", "0",
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
    """Read a segment's actual embedded end PTS (seconds), from its audio track.

    See probe_start_timestamp() -- audio is the sync reference, so this
    anchors splices to audio's own real PTS rather than video's.

    Unlike probe_start_timestamp(), this can't cheaply skip to "the last
    packet" with -read_intervals -- MPEG-TS isn't seekable that way without
    already knowing a byte offset -- so it reads every audio packet and takes
    the last one. Fine for segments this small (a few dozen frames); would
    need a different approach for anything long enough to make a full read
    expensive.
    """
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "packet=pts_time",
        "-of", "csv=p=0",
        ts_path,
    ], check=True, capture_output=True, text=True)
    last_line = result.stdout.strip().splitlines()[-1]
    return float(last_line.split(",")[0])

def rendition_segment_duration(size, fps):
    """The duration every filler segment should declare (EXTINF, tsoffset stepping).

    Probed from audio's own real packet timestamps in segment 0, rather than
    a file's container-level format=duration (the previous approach) or a
    plain calculation from AUDIO_FRAMES/AUDIO_SAMPLE_RATE (rejected in favor
    of this -- probing the real file self-corrects if the encode parameters
    ever change, instead of silently drifting out of sync with a parallel
    hardcoded formula). format=duration turned out not to be either track's
    own real span at all -- it's the union of both (audio starts ~21ms
    before video, video ends ~20ms after audio), which overstates both.
    Declaring audio's own exact probed span here instead means every filler
    segment's declared duration matches what audio actually contains
    exactly, which is what mattered: audio is the sync reference (see
    probe_start_timestamp()), and a real MLB.tv session showed the previous,
    inflated value causing an audio timestamp gap that grew by ~19.7ms every
    single filler segment for the length of an ad break (confirmed via
    chrome://media-internals). Video is left with a small, non-growing
    ~1.7ms/segment residual instead -- a few ms of video jitter is far less
    perceptible than an unbounded, compounding audio gap.

    probe_end_timestamp() returns where the *last* audio packet starts, not
    where it ends, so its own frame duration (1024 samples) has to be added
    back on to get the segment's real total span.
    """
    ts_path = os.path.join(rendition_dir(size, fps), "filler_000.ts")
    last_frame_duration = 1024 / AUDIO_SAMPLE_RATE
    return probe_end_timestamp(ts_path) - probe_start_timestamp(ts_path) + last_frame_duration

def verify_segment_durations():
    """Walk every generated filler segment on disk and confirm they all share one duration.

    rendition_segment_duration() probes segment 0 and assumes that speaks
    for every segment in the rendition -- this actually checks that
    assumption (via each segment's own *encoded* container duration, a
    different measure than what rendition_segment_duration() returns, but
    one that should still land in the same place across every segment
    regardless) across the whole assets/filler/ tree instead of just
    trusting it. Returns the shared duration if every segment agrees (within
    a small tolerance for floating-point noise); raises otherwise.
    """
    durations = {}  # ts_path -> measured duration, kept around for the error message

    logger.info("checking segment durations...")
    for root, _dirs, files in os.walk(OUTPUT_DIR):
        for filename in files:
            if not filename.endswith(".ts"):
                continue
            ts_path = os.path.join(root, filename)
            duration = probe_duration(ts_path)
            logger.info(f"{ts_path}: {duration:.6f}s")
            durations[ts_path] = duration

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

    else:
        logger.info(f"all consistent at {reference}")

def generate_rendition(size, fps, bitrate_bps, profile, level_idc):
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
    for seconds_remaining in range(MAX_SECONDS, -121, -1):
        ts_path = os.path.join(output_dir, f"filler_{seconds_remaining:03d}.ts")

        frame = make_filler_frame(seconds_remaining, size)
        frame.save(TMP_PNG)

        encode_ts(TMP_PNG, ts_path, size, fps, cumulative_offset, bitrate_bps, profile, level_idc)
        generated_count += 1

        # drive the next segment's offset from this segment's *actual* measured
        # duration, not a fixed nominal value, so drift never accumulates
        cumulative_offset += probe_duration(ts_path)

    if os.path.exists(TMP_PNG):
        os.remove(TMP_PNG)
    if os.path.exists(TMP_VIDEO_TS):
        os.remove(TMP_VIDEO_TS)

    # record the segments' real encoded duration once, so callers (e.g. the
    # live playlist rewriter) can read it directly instead of shelling out
    # to ffprobe on every request
    duration = rendition_segment_duration(size, fps)
    with open(os.path.join(output_dir, "EXTINF"), "w") as f:
        f.write(f"{duration:.6f}")

    elapsed = time.perf_counter() - start
    logger.info(f"generated {generated_count} {duration:.6f}s segments into {output_dir} in {elapsed:.1f}s")

def ensure_rendition(size, fps, average_bandwidth, codecs):
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
    if rendition_exists(size, fps):
        logger.debug(f"filler segments in {rendition_dir(size, fps)} already exist, skipping")
    else:
        bitrate_bps = int(average_bandwidth * FILLER_BITRATE_HEADROOM)
        profile, level_idc = parse_h264_profile_level(codecs)
        generate_rendition(size, fps, bitrate_bps, profile, level_idc)

    extinf_path = os.path.join(rendition_dir(size, fps), "EXTINF")
    with open(extinf_path) as f:
        return float(f.read().strip())
