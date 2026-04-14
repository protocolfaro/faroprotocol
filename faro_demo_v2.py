#!/usr/bin/env python3
"""
faro_demo_v2.py  -  outputs/faro_demo_v2.mp4
Concatena los 5 videos AZ de referencia -> 1920x1080 30fps
"""
import os
from moviepy import VideoFileClip, concatenate_videoclips

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(ROOT, "outputs", "faro_demo_v2.mp4")
W, H = 1920, 1080

AZ_VIDEOS = [
    "C:/Users/Usuario/Downloads/AZ2M0JICD_sl8kyxm-VLlg-AZ2M0JIC-iQ_5Tq-kjGzDw.mp4",
    "C:/Users/Usuario/Downloads/AZ2M34vnaBIqXsjDnZ8Kag-AZ2M34vnF7eiDFmtn1YbVw.mp4",
    "C:/Users/Usuario/Downloads/AZ2NItNgp3CqYThv6sgI2g-AZ2NItNgFqSE7eY-e12CEg.mp4",
    "C:/Users/Usuario/Downloads/AZ2NJzorfI8esHYoEeumLg-AZ2NJzorufx14XKCDT_yCg.mp4",
    "C:/Users/Usuario/Downloads/AZ2NKd1UMAmFEbJ_33U4bw-AZ2NKd1U98tDP6XwVOuklw.mp4",
]

def main():
    os.makedirs(os.path.join(ROOT, "outputs"), exist_ok=True)

    clips = []
    for i, path in enumerate(AZ_VIDEOS):
        c = VideoFileClip(path).resized((W, H))
        print(f"  [{i+1}] {c.duration:.1f}s  {path.split('/')[-1][:30]}")
        clips.append(c)

    total = sum(c.duration for c in clips)
    print(f"Total: {total:.1f}s  /  {len(clips)} clips")
    print(f"Exportando -> {OUT}")

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(
        OUT, fps=30, codec="libx264", audio=True,
        preset="medium",
        ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"],
        logger="bar",
    )
    for c in clips:
        c.close()

    sz = os.path.getsize(OUT) / 1e6
    print(f"[OK] {sz:.1f} MB  ->  {OUT}")

if __name__ == "__main__":
    main()
