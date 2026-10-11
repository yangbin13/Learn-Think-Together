"""Encode an edited real-browser screencast with local Chinese narration.

This is artifact processing only: it never drives a browser or invents UI states.
Raw CUA frames stay in the private recording folder and are not distributed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATE = "20261011"
WIDTH, HEIGHT, FPS = 1440, 900, 24


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def seconds_stamp(value, ass=False):
    millis = round(value * (100 if ass else 1000))
    base = 100 if ass else 1000
    hour, rest = divmod(millis, 3600 * base)
    minute, rest = divmod(rest, 60 * base)
    second, fraction = divmod(rest, base)
    return f"{hour}:{minute:02}:{second:02}.{fraction:02}" if ass else f"{hour:02}:{minute:02}:{second:02},{fraction:03}"


def caption_chunks(text):
    result = []
    for sentence in re.findall(r"[^。！？!?]+[。！？!?]?", text):
        sentence = sentence.strip()
        while len(sentence) > 48:
            cut = sentence.rfind("，", 16, 45) + 1
            cut = cut if cut > 0 else 40
            result.append(sentence[:cut])
            sentence = sentence[cut:]
        if sentence:
            result.append(sentence)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, default=ROOT / f"data/release-recording-{DATE}")
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / "data/release-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")
    args = parser.parse_args()
    folder = args.recordings.resolve()
    capture = read_json(folder / "capture.json")
    storyboard = read_json(folder / "storyboard.json")
    narrations = read_json(folder / "narration-clips.json")
    milestones = {item["label"]: item["index"] for item in capture["milestones"]}
    raw_frames = {item["index"]: item for item in capture["frames"]}
    rendered = folder / "rendered"
    rendered.mkdir(exist_ok=True)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.is_file():
        raise RuntimeError("A Chinese font is required; do not render missing-glyph boxes")
    title_font = ImageFont.truetype(str(font_path), 24)
    note_font = ImageFont.truetype(str(font_path), 16)
    scene_info, image_entries, subtitle_entries = [], [], []
    cursor = 0.0
    waves = []
    wav_params = None
    for index, scene in enumerate(storyboard["scenes"]):
        if scene["start"] not in milestones or scene["end"] not in milestones:
            raise RuntimeError(f"Missing actual recorded state for scene {scene['title']}")
        with wave.open(str(narrations[index]["path"]), "rb") as source:
            params = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            audio = source.readframes(source.getnframes())
            speech_seconds = source.getnframes() / source.getframerate()
        if wav_params is None:
            wav_params = params
        if params != wav_params or params[:2] != (1, 2):
            raise RuntimeError("Narration must have consistent mono PCM16 parameters")
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float64)
        if not len(samples) or np.sqrt(np.mean(samples * samples)) < 30:
            raise RuntimeError("Narration is silent")
        duration = max(float(scene["seconds"]), math.ceil(speech_seconds + 1.0))
        start, end = milestones[scene["start"]], milestones[scene["end"]]
        indices = list(range(start, end + 1))
        if scene.get("final_state"):
            indices.append(milestones[scene["final_state"]])
        # Cut the waiting time; keep a readable final state for most of the scene.
        if len(indices) == 1:
            durations = [duration]
        elif len(indices) == 2:
            durations = [min(3.0, duration * .2), duration - min(3.0, duration * .2)]
        else:
            first = min(2.0, duration * .15)
            middle = min(5.0, duration * .25)
            durations = [first] + [middle / (len(indices) - 2)] * (len(indices) - 2) + [duration - first - middle]
        for frame_index, hold in zip(indices, durations):
            original = Path(raw_frames[frame_index]["path"]).resolve()
            if original.parent != folder:
                raise RuntimeError("Recorded frame path escapes the declared recording folder")
            screenshot = Image.open(original).convert("RGB")
            scaled_height = round(screenshot.height * 1412 / screenshot.width)
            screenshot = screenshot.resize((1412, scaled_height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (WIDTH, HEIGHT), "#142d29")
            canvas.paste(screenshot, (14, 0))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 795, WIDTH, HEIGHT), fill="#142d29")
            draw.text((28, 801), scene["title"], font=title_font, fill="#f3f5eb")
            draw.text((1080, 808), "真实界面 · 等待已剪辑", font=note_font, fill="#cbd7cc")
            target = rendered / f"scene-{index:02}-frame-{frame_index:05}.png"
            canvas.save(target)
            image_entries.append((target, hold))
        chunks = caption_chunks(scene["speech"])
        char_count = sum(len(chunk) for chunk in chunks)
        offset = cursor + .3
        for chunk in chunks:
            part_seconds = speech_seconds * len(chunk) / char_count
            subtitle_entries.append((offset, offset + part_seconds, chunk))
            offset += part_seconds
        waves.append((cursor + .3, audio))
        scene_info.append({"index": index, "title": scene["title"], "start_seconds": cursor,
                           "duration_seconds": duration, "narration_seconds": round(speech_seconds, 3),
                           "first_captured_frame": start, "last_captured_frame": indices[-1],
                           "captured_frame_indices": indices})
        cursor += duration
    if not 180 <= cursor <= 300:
        raise RuntimeError(f"Contest video must be 180-300 seconds; planned {cursor}")
    joined = bytearray(round(cursor * wav_params[2]) * 2)
    for offset, audio in waves:
        byte_offset = round(offset * wav_params[2]) * 2
        joined[byte_offset:byte_offset + len(audio)] = audio
    with wave.open(str(folder / "narration.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(wav_params[2])
        output.writeframes(joined)
    OUT = ROOT / "deliverables"
    OUT.mkdir(exist_ok=True)
    srt_path = OUT / f"学思同行-最终演示-{DATE}.srt"
    srt = "\n\n".join(f"{i+1}\n{seconds_stamp(start)} --> {seconds_stamp(end)}\n{text}" for i, (start, end, text) in enumerate(subtitle_entries)) + "\n"
    srt_path.write_text(srt, encoding="utf-8")
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1440
PlayResY: 900
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Microsoft YaHei,26,&H00FFFFFF,&H00FFFFFF,&H00302514,&H00302514,0,0,0,0,100,100,0,0,1,1,0,2,28,28,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_lines = []
    for start, end, text in subtitle_entries:
        if len(text) > 36:
            cut = text.rfind("，", 12, 30) + 1
            cut = cut if cut > 0 else 28
            text = text[:cut] + r"\N" + text[cut:]
        ass_lines.append(f"Dialogue: 0,{seconds_stamp(start, True)},{seconds_stamp(end, True)},Caption,,0,0,0,,{text}")
    (folder / "release-subtitles.ass").write_text(ass_header + "\n".join(ass_lines) + "\n", encoding="utf-8-sig")
    concat = "\n".join(f"file '{path.as_posix()}'\nduration {duration:.9f}" for path, duration in image_entries)
    concat += f"\nfile '{image_entries[-1][0].as_posix()}'\n"
    (folder / "frames.txt").write_text(concat, encoding="utf-8")
    video = OUT / f"学思同行-最终演示-{DATE}.mp4"
    command = [str(args.ffmpeg.resolve()), "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0",
               "-i", "frames.txt", "-i", "narration.wav", "-vf", f"fps={FPS},ass=release-subtitles.ass", "-fps_mode", "cfr", "-t", str(cursor),
               "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-threads", "2",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(video)]
    with (folder / "encoding.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=folder, stdout=log, stderr=log, check=True)
    decoded = subprocess.run([str(args.ffmpeg.resolve()), "-v", "error", "-i", str(video), "-f", "null", "-"], capture_output=True, check=False)
    probe = subprocess.run([str(args.ffmpeg.resolve()), "-hide_banner", "-i", str(video)], capture_output=True, check=False)
    probe_text = probe.stderr.decode("utf-8", "replace")
    reader = cv2.VideoCapture(str(video))
    fps = reader.get(cv2.CAP_PROP_FPS)
    count = int(reader.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH)), int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    reader.release()
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe_text)
    container_seconds = sum(float(value) * factor for value, factor in zip(duration_match.groups(), (3600, 60, 1))) if duration_match else 0
    if not fps or abs(count / fps - cursor) > .1 or abs(container_seconds - cursor) > .1:
        raise RuntimeError("Encoded frames and container must cover the entire narrated storyboard")
    media = {"path": video.relative_to(ROOT).as_posix(), "bytes": video.stat().st_size,
             "sha256": hashlib.sha256(video.read_bytes()).hexdigest(), "duration_seconds": round(count / fps, 3),
             "width": width, "height": height, "fps": fps, "frames": count, "container_duration_seconds": container_seconds,
             "video_codec": "h264" if "Video: h264" in probe_text else "unknown",
             "audio_codec": "aac" if "Audio: aac" in probe_text else "unknown"}
    verification = {"version": DATE, "ready": False, "media": media,
                    "full_decode_passed": decoded.returncode == 0 and not decoded.stderr.strip(),
                    "recording_method": capture["method"], "response_bodies_modified": False,
                    "narration": "Local Windows SAPI Chinese voice from self-authored script",
                    "voice": narrations[0]["voice"], "scenes": scene_info,
                    "manual_visual_review_pending": True,
                    "scope": "Original public 408 course; private textbooks, auth and configuration excluded"}
    evidence_path = ROOT / f"docs/validation/demo-final-verification-{DATE}.json"
    evidence_path.write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    explanation = ["# 演示解说与复现", "", "真实 CUA 浏览器操作截图组成的演示视频；等待经过剪辑压缩，未改写任何回答正文。中文解说为本机 Windows SAPI 合成。", ""]
    for scene, facts in zip(storyboard["scenes"], scene_info):
        explanation += [f"## {facts['start_seconds']:.0f} 秒 · {scene['title']}", "", scene["speech"], ""]
    explanation += ["## 本机重制", "", "需要自己保留的 CUA 原始截图及 capture.json、storyboard.json；它们属于录制素材，不是应用运行依赖。先执行 tools/make_release_narration.ps1，再用具备 Pillow、NumPy、OpenCV 的 Python 执行 tools/assemble_release_demo.py，并通过 --ffmpeg 指定本机 FFmpeg。原始素材与工具运行时不随源码分发。", ""]
    (OUT / f"学思同行-演示解说与复现-{DATE}.md").write_text("\n".join(explanation), encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
