from flask import Flask, request, jsonify, send_file
import os
import subprocess
import tempfile
import urllib.request
import uuid

app = Flask(__name__)

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
    return (text.replace("{", "(").replace("}", ")")
                .replace("\\", "").replace("\n", " ").strip())


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
        (" ".join(t for t, _, _ in g), g[0][1], g[-1][2])
        for g in groups
    ]


def write_ass(captions, path, uppercase=False):
    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)

        for i, (text, start, end) in enumerate(captions):
            if i + 1 < len(captions):
                end = min(end, captions[i + 1][1])

            if uppercase:
                text = _tr_upper(text)

            f.write(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},"
                f"Default,,0,0,0,,{text}\n"
            )


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "n8n-ffmpeg-renderer"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy"
    })


@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json()

    images = data.get("images", [])
    audio_url = data.get("audio_url")

    words = data.get("words")
    subtitle_uppercase = bool(
        data.get("subtitle_uppercase", False)
    )

    if len(images) != 6:
        return jsonify({
            "error": "Exactly 6 images are required"
        }), 400

    if not audio_url:
        return jsonify({
            "error": "audio_url is required"
        }), 400

    workdir = tempfile.mkdtemp()
    job_id = str(uuid.uuid4())

    try:
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

            image_files.append(path)

        audio_path = os.path.join(
            workdir,
            "audio.mp3"
        )

        urllib.request.urlretrieve(
            audio_url,
            audio_path
        )

        concat_path = os.path.join(
            workdir,
            "images.txt"
        )

        with open(concat_path, "w") as f:
            for image in image_files:
                f.write(
                    f"file '{image}'\n"
                )
                f.write(
                    "duration 5\n"
                )

            f.write(
                f"file '{image_files[-1]}'\n"
            )

        output_path = os.path.join(
            workdir,
            f"{job_id}.mp4"
        )

        vf = (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30"
        )

        captions = (
            build_captions(words)
            if isinstance(words, list)
            else []
        )

        if captions:
            subtitle_path = os.path.join(
                workdir,
                "subs.ass"
            )

            write_ass(
                captions,
                subtitle_path,
                subtitle_uppercase
            )

            vf += ",ass=subs.ass"

        command = [
            "ffmpeg",
            "-y",
            "-nostats",
            "-loglevel", "error",
            "-threads", "2",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,
            "-i", audio_path,
            "-vf", vf,
            "-filter_threads", "1",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "stillimage",
            "-crf", "23",
            "-threads", "2",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]

        subprocess.run(
            command,
            check=True,
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=540
        )

        return send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="short.mp4"
        )

    except subprocess.TimeoutExpired:
        return jsonify({
            "error": "Render timed out"
        }), 504

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg failed",
            "details": e.stderr.decode(
                errors="ignore"
            )
        }), 500

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


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
