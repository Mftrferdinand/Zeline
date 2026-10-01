# Video Frames

> Extract frames or short clips from videos using ffmpeg.

Extract a single frame from a video, or create quick thumbnails for inspection.

## Quick start

Requires Bash and ffmpeg on PATH. Locate this skill's installed directory
(`$ZELINE_HOME/skills/public/video-frames`, default `~/.zeline/skills/public/video-frames`).
Use Bash explicitly: installed companion files need not have an executable bit.

```bash
SKILL_DIR="${ZELINE_HOME:-$HOME/.zeline}/skills/public/video-frames"
```

First frame:

```bash
bash "$SKILL_DIR/scripts/frame.sh" /path/to/video.mp4 --out "${TMPDIR:-/tmp}/frame.jpg"
```

At a timestamp:

```bash
bash "$SKILL_DIR/scripts/frame.sh" /path/to/video.mp4 --time 00:00:10 --out "${TMPDIR:-/tmp}/frame-10s.jpg"
```

## Notes

- Prefer `--time` for "what is happening around here?".
- Use a `.jpg` for quick share; use `.png` for crisp UI frames.
