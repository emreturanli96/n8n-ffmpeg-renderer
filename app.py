from flask import Flask, request, jsonify, send_file
import math
import os
import subprocess
import tempfile
import time
import urllib.request
import uuid

app = Flask(__name__)

# --- Fixed output settings ----------------------------------------------------

WIDTH = 1080
HEIGHT = 1920
FPS = 30
SLIDE_SECONDS = 5
IMAGE_COUNT = 6
TOTAL_FRAMES = IMAGE_COUNT * SLIDE_SECONDS * FPS
RENDER_BUDGET = 540


# --- Subtitle style -----------------------------------------------------------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(t):
    cs = int(round(max(0.0, float(t)) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text):
    return (
        text.replace("{", "(")
        .replace("}", ")")
        .replace("\\", "")
        .replace("\n", " ")
        .strip()
    )


def _tr_upper(text):
    return text.replace("i", "İ").replace("ı", "I").upper()


def build_captions(words, max_words=3, max_gap=0.6, max_len=1.6):
    items = []

    for w in words or []:
        if not isinstance(w, dict) or w.get("type", "word") != "word":
            continue

        try:
            start = float(w["start"])
            end = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue

        text = _clean(str(w.get("text", "")))

        if text and end > start:
            items.append((text, start, end))

    groups = []
    cur = []

    for text, start, end in items:
        if cur and (
            len(cur) >= max_words
            or start - cur[-1][2] > max_gap
            or end - cur[0][1] > max_len
        ):
            groups.append(cur)
            cur = []

        cur.append((text, start, end))

    if cur:
        groups.append(cur)

    return [
        (
            " ".join(t for t, _, _ in g),
            g[0][1],
            g[-1][2]
        )
        for g in groups
    ]


def _cs(t):
    return int(round(max(0.0, float(t)) * 100)) / 100.0


def _frame_at(t):
    return min(
        TOTAL_FRAMES,
        int(math.ceil(t * FPS - 1e-6))
    )


def build_timeline(captions, uppercase=False):
    caps = []

    for i, (text, start, end) in enumerate(captions):
        if i + 1 < len(captions):
            end = min(
                end,
                captions[i + 1][1]
            )

        f0 = _frame_at(_cs(start))
        f1 = _frame_at(_cs(end))

        if f1 > f0:
            caps.append(
                (
                    _tr_upper(text) if uppercase else text,
                    f0,
                    f1
                )
            )

    cuts = {
        0,
        TOTAL_FRAMES
    }

    cuts.update(
        k * SLIDE_SECONDS * FPS
        for k in range(1, IMAGE_COUNT)
    )

    for _, f0, f1 in caps:
        cuts.add(f0)
        cuts.add(f1)

    cuts = sorted(
        c for c in cuts
        if 0 <= c <= TOTAL_FRAMES
    )

    segments = []

    for a, b in zip(cuts, cuts[1:]):
        if b <= a:
            continue

        img = min(
            a // (SLIDE_SECONDS * FPS),
            IMAGE_COUNT - 1
        )

        cap = None

        for ci, (_, f0, f1) in enumerate(caps):
            if f0 <= a < f1:
                cap = ci
                break

        if (
            segments
            and segments[-1][0] == img
            and segments[-1][1] == cap
        ):
            segments[-1][3] = b

        else:
            segments.append(
                [
                    img,
                    cap,
                    a,
                    b
                ]
            )

    return (
        [c[0] for c in caps],
        segments
    )


def _run(cmd, cwd, deadline):
    remaining = deadline - time.monotonic()

    if remaining <= 0:
        raise subprocess.TimeoutExpired(
            cmd,
            0
        )

    subprocess.run(
        cmd,
        check=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=remaining
    )


FFMPEG_BASE = [
    "ffmpeg",
    "-y",
    "-nostats",
    "-loglevel",
    "error",
    "-threads",
    "1"
]


def render_base(
    src,
    dst,
    workdir,
    deadline
):
    _run(
        FFMPEG_BASE + [
            "-i",
            src,

            "-vf",
            (
                f"scale={WIDTH}:{HEIGHT}:"
                "force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT}"
            ),

            "-filter_threads",
            "1",

            "-frames:v",
            "1",

            dst
        ],
        workdir,
        deadline
    )


def render_captioned(
    base,
    text,
    ass_name,
    dst,
    workdir,
    deadline
):
    with open(
        os.path.join(
            workdir,
            ass_name
        ),
        "w",
        encoding="utf-8"
    ) as f:

        f.write(ASS_HEADER)

        f.write(
            f"Dialogue: 0,"
            f"{_ass_time(0)},"
            f"{_ass_time(10)},"
            f"Default,,0,0,0,,"
            f"{text}\n"
        )

    _run(
        FFMPEG_BASE + [
            "-i",
            base,

            "-vf",
            f"ass={ass_name}",

            "-filter_threads",
            "1",

            "-frames:v",
            "1",

            dst
        ],
        workdir,
        deadline
    )


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "status": "ok",
            "service": "n8n-ffmpeg-renderer"
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "healthy"
        }
    )


@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json()

    images = data.get(
        "images",
        []
    )

    audio_url = data.get(
        "audio_url"
    )

    words = data.get(
        "words"
    )

    subtitle_uppercase = bool(
        data.get(
            "subtitle_uppercase",
            False
        )
    )

    if len(images) != 6:
        return jsonify(
            {
                "error":
                "Exactly 6 images are required"
            }
        ), 400

    if not audio_url:
        return jsonify(
            {
                "error":
                "audio_url is required"
            }
        ), 400

    workdir = tempfile.mkdtemp()
    job_id = str(uuid.uuid4())

    deadline = (
        time.monotonic()
        + RENDER_BUDGET
    )

    try:

        # ----------------------------------------------------
        # 1. Download images
        # ----------------------------------------------------

        image_files = []

        for i, url in enumerate(images):

            path = os.path.join(
                workdir,
                f"image_{i+1}.jpg"
            )

            urllib.request.urlretrieve(
                url,
                path
            )

            image_files.append(
                path
            )

        # ----------------------------------------------------
        # 2. Download audio
        # ----------------------------------------------------

        audio_path = os.path.join(
            workdir,
            "audio.mp3"
        )

        urllib.request.urlretrieve(
            audio_url,
            audio_path
        )

        # ----------------------------------------------------
        # 3. Create six base 1080x1920 frames
        # ----------------------------------------------------

        base_files = []

        for i, src in enumerate(
            image_files
        ):

            dst = (
                f"base_{i+1}.png"
            )

            render_base(
                src,
                dst,
                workdir,
                deadline
            )

            base_files.append(
                dst
            )

        # ----------------------------------------------------
        # 4. Build subtitle timeline
        # ----------------------------------------------------

        captions = (
            build_captions(words)
            if isinstance(
                words,
                list
            )
            else []
        )

        texts, segments = (
            build_timeline(
                captions,
                subtitle_uppercase
            )
        )

        # ----------------------------------------------------
        # 5. Create captioned still images
        # ----------------------------------------------------

        still_for = {}

        for img, cap, _, _ in segments:

            key = (
                img,
                cap
            )

            if key in still_for:
                continue

            if cap is None:

                still_for[key] = (
                    base_files[img]
                )

            else:

                n = (
                    len(still_for)
                    + 1
                )

                dst = (
                    f"still_{n:03d}.png"
                )

                render_captioned(
                    base_files[img],
                    texts[cap],
                    f"cap_{n:03d}.ass",
                    dst,
                    workdir,
                    deadline
                )

                still_for[key] = dst

        # ----------------------------------------------------
        # 6. Build concat timeline
        # ----------------------------------------------------

        concat_path = os.path.join(
            workdir,
            "stills.txt"
        )

        with open(
            concat_path,
            "w"
        ) as f:

            for (
                img,
                cap,
                f0,
                f1
            ) in segments:

                f.write(
                    "file "
                    f"'{still_for[(img, cap)]}'"
                    "\n"
                )

                f.write(
                    "duration "
                    f"{(f1 - f0) / FPS:.6f}"
                    "\n"
                )

            last = segments[-1]

            f.write(
                "file "
                f"'{still_for[(last[0], last[1])]}'"
                "\n"
            )

        # ----------------------------------------------------
        # 7. Final video output
        # ----------------------------------------------------

        output_path = os.path.join(
            workdir,
            f"{job_id}.mp4"
        )

        _run(
            FFMPEG_BASE + [

                "-f",
                "concat",

                "-safe",
                "0",

                "-i",
                "stills.txt",

                "-i",
                audio_path,

                "-vf",
                f"fps={FPS},format=yuv420p",

                "-filter_threads",
                "1",

                "-c:v",
                "libx264",

                "-preset",
                "ultrafast",

                "-tune",
                "stillimage",

                "-crf",
                "23",

                "-threads",
                "1",

                "-pix_fmt",
                "yuv420p",

                "-c:a",
                "aac",

                "-b:a",
                "128k",

                "-shortest",

                "-movflags",
                "+faststart",

                output_path
            ],
            workdir,
            deadline
        )

        # ----------------------------------------------------
        # 8. Return MP4
        # ----------------------------------------------------

        return send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="short.mp4"
        )

    except subprocess.TimeoutExpired:

        return jsonify(
            {
                "error":
                "Render timed out"
            }
        ), 504

    except subprocess.CalledProcessError as e:

        return jsonify(
            {
                "error":
                "FFmpeg failed",

                "details":
                e.stderr.decode(
                    errors="ignore"
                )
            }
        ), 500

    except Exception as e:

        return jsonify(
            {
                "error":
                str(e)
            }
        ), 500


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
