from flask import Flask, request, jsonify, send_file
import os
import subprocess
import tempfile
import urllib.request
import uuid

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "n8n-ffmpeg-renderer"
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})

@app.route("/render", methods=["POST"])
def render_video():
    data = request.get_json()

    images = data.get("images", [])
    audio_url = data.get("audio_url")

    if len(images) != 6:
        return jsonify({"error": "Exactly 6 images are required"}), 400

    if not audio_url:
        return jsonify({"error": "audio_url is required"}), 400

    workdir = tempfile.mkdtemp()
    job_id = str(uuid.uuid4())

    try:
        image_files = []

        for i, url in enumerate(images):
            path = os.path.join(workdir, f"image_{i+1}.jpg")
            urllib.request.urlretrieve(url, path)
            image_files.append(path)

        audio_path = os.path.join(workdir, "audio.mp3")
        urllib.request.urlretrieve(audio_url, audio_path)

        concat_path = os.path.join(workdir, "images.txt")

        with open(concat_path, "w") as f:
            for image in image_files:
                f.write(f"file '{image}'\n")
                f.write("duration 5\n")

            f.write(f"file '{image_files[-1]}'\n")

        output_path = os.path.join(workdir, f"{job_id}.mp4")

        command = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_path,
            "-i", audio_path,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        return send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="short.mp4"
        )

    except subprocess.CalledProcessError as e:
        return jsonify({
            "error": "FFmpeg failed",
            "details": e.stderr.decode(errors="ignore")
        }), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
