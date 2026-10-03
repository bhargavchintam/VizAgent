# Demo video

**[Watch on Google Drive](https://drive.google.com/file/d/1K9Tf1ObIpW3gEpsApN-hMGbWA5NjscKa/view?usp=sharing)**
· [vizagent_demo.mp4](vizagent_demo.mp4) (2:00, 1080p) · captions: [vizagent_demo.srt](vizagent_demo.srt)

A two-minute walkthrough of ViZ Agent: the problem, how the agent works, a live sweep (60 candidate
clips, 4 verified), a real street-camera clip, the Cosmos second look, engineer review, the drafted
work order and the W&B Weave traces.

## How it was made

- **Visuals:** [HeyGen HyperFrames](https://hyperframes.heygen.com), an HTML + GSAP composition rendered
  to MP4 in headless Chrome. Source in [`hyperframes/`](hyperframes/): `build.py` writes `index.html`.
- **Voice:** ElevenLabs v4 text-to-speech, the "Bella" voice, one take per scene with audio tags that
  carry the story (`[curious]`, `[serious]`, `[sighs]`, `[excited]`, `[dramatically]`, `[reassuring]`,
  `[proudly]` ...). The takes are in [`hyperframes/voice_el/`](hyperframes/voice_el/).
- **Screen and voice work together:** the screen shows one number or label, the voice tells the story.
- **Footage:** screenshots of the live app and one real 5-second clip from a San Francisco street camera.
  The clip is team footage, so it is not in git.

## Rebuild

Needs Node, Python 3 with Pillow, ffmpeg and Google Chrome.

```bash
cd demo/hyperframes
npm install
VIZ_CLIP=/path/to/clip_sf4.mp4 python build.py
npx hyperframes render --output renders/vizagent_demo.mp4
```

`build.py` lays the scene takes on a timeline, finds each caption line from the pauses in the voice,
writes `narration.wav`, `vizagent_demo.srt` and `index.html`, and extracts the clip frames. Without the
ElevenLabs takes it falls back to macOS `say`.
