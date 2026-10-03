"""Build the HyperFrames composition for the ViZ Agent demo video (v3).

The voice tells the story; the screen shows only the one number or label that backs it up.
Narration: one ElevenLabs v4 take per scene in voice_el/s01.mp3 ... s11.mp3 (Bella voice, with
audio tags). Without those files it falls back to macOS `say`, one file per line, in voice/.
Writes narration.wav, vizagent_demo.srt, assets/clip/ and index.html.

    npm install                      # hyperframes + an ffprobe binary
    VIZ_CLIP=path/to/clip_sf4.mp4 python build.py
    npx hyperframes render --output renders/vizagent_demo.mp4

assets/*.jpg are crops of the live app's screenshots (VIZ_SHOTS re-crops them from the originals).
The street clip is team footage, so it is not in git: VIZ_CLIP points at a local copy, downloaded
once through the app's /api/stream route. ffmpeg comes from bin/ if present, else from PATH.
"""

import hashlib
import html
import os
import re
import shutil
import subprocess
import textwrap
import wave
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
VOICE = HERE / "voice"
EL_VOICE = HERE / "voice_el"
EL_RATE = 44100
EL_LEAD, EL_TAIL = 0.45, 0.55
SHOTS = Path(os.environ["VIZ_SHOTS"]) if os.environ.get("VIZ_SHOTS") else None
CLIP = Path(os.environ.get("VIZ_CLIP", HERE / "clip_sf4.mp4"))
FFMPEG = HERE / "bin" / "ffmpeg" if (HERE / "bin" / "ffmpeg").exists() else Path(shutil.which("ffmpeg") or "ffmpeg")
RATE = 22050
LEAD, GAP, TAIL = 0.7, 0.35, 0.9
CLIP_FPS = 15
TEAM = ["Bindu Bhargava Reddy Chintam", "Sripadha V", "Jacob Shrader"]

SHOT_SPECS = {
    "app": ("mcp-claude-in-chrome-blob-1790980950661-20hfdi.jpg", (230, 0, 1290, 520)),
    "hot": ("mcp-claude-in-chrome-blob-1790981006381-sj0k2d.jpg", (230, 385, 1290, 615)),
    "review": ("mcp-claude-in-chrome-blob-1790981145473-tnfjj8.jpg", (240, 60, 1270, 650)),
    "order": ("mcp-claude-in-chrome-blob-1790981169330-c6c87b.jpg", (240, 110, 1275, 610)),
    "weave": ("mcp-claude-in-chrome-blob-1790981197101-94ebt9.jpg", (58, 0, 1568, 292)),
}

# (caption, spoken form or None). Spoken lines add to the screen; they never read it out.
SCENES = [
    ("title", [
        ("Cities already record hours of street video.", None),
        ("ViZ Agent watches it, so city engineers don't have to.", "Viz Agent watches it, so city engineers don't have to."),
    ]),
    ("problem", [
        ("San Francisco pledged Vision Zero in 2014.", None),
        ("Twelve years later, serious crashes are higher, not lower.", None),
        ("A new city law now asks for progress reports every quarter.", None),
        ("The close calls are already on camera. Nobody has time to watch them.", None),
    ]),
    ("how", [
        ("So we built an agent that does the watching.", None),
        ("It searches the indexed video for four kinds of pedestrian conflict.", None),
        ("Every candidate must pass three independent checks before it counts.", None),
        ("Then it ranks the locations and drafts a fix for the worst one.", None),
    ]),
    ("sweep", [
        (("In our live run, the sweep pulled sixty candidate clips from a Toronto dashcam "
          "and four San Francisco street cameras."), None),
        ("Fifty-six were thrown out.", None),
        ("So an engineer reviews four clips, not sixty.", None),
    ]),
    ("hot", [
        ("What survives is grouped by camera, so the worst location rises to the top.", None),
    ]),
    ("clip", [
        ("This is one of the verified clips, straight from a San Francisco street camera.", None),
    ]),
    ("second", [
        ("For a second opinion, Cosmos Reason watches the clip again and explains what physically happened.", None),
        ("It agrees: this is a real conflict.", None),
    ]),
    ("review", [
        ("An engineer confirms or rejects each clip with one click.", None),
        ("Those labels measure the agent's precision and become a dataset for the next model.", None),
    ]),
    ("order", [
        ("Approved clips turn into a drafted work order.", None),
        ("The cause of the conflict picks a proven federal safety fix, ready to file with SF311.",
         "The cause of the conflict picks a proven federal safety fix, ready to file with S F 3 1 1."),
        ("A person still signs off before anything is sent.", None),
    ]),
    ("weave", [
        ("Every search, check and model call is traced in Weights & Biases Weave, so each decision can be audited.",
         "Every search, check and model call is traced in Weights and Biases Weave, so each decision can be audited."),
    ]),
    ("close", [
        ("Less time watching video. More time fixing streets.", None),
        ("Built by Team 20 for the VAST Builders Challenge.", None),
    ]),
]


def speak(text):
    VOICE.mkdir(exist_ok=True)
    out = VOICE / f"{hashlib.sha1(text.encode()).hexdigest()[:12]}.wav"
    if not out.exists():
        subprocess.run(["say", "-v", "Samantha", "-r", "178", "--file-format=WAVE",
                        f"--data-format=LEI16@{RATE}", "-o", str(out), text], check=True)
    with wave.open(str(out)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        return out, w.getnframes() / RATE


def eleven_takes():
    """The ElevenLabs take for every scene as 44.1 kHz mono wav, or None if any is missing."""
    mp3s = [EL_VOICE / f"s{i:02d}.mp3" for i in range(1, len(SCENES) + 1)]
    if not all(p.exists() for p in mp3s):
        return None
    for mp3 in mp3s:
        if not mp3.with_suffix(".wav").exists():
            subprocess.run([str(FFMPEG), "-loglevel", "error", "-y", "-i", str(mp3), "-ac", "1",
                            "-ar", str(EL_RATE), str(mp3.with_suffix(".wav"))], check=True)
    return [p.with_suffix(".wav") for p in mp3s]


def wav_secs(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def silences(wav, secs):
    """(start, end) of each quiet stretch in a take, from ffmpeg's silencedetect."""
    log = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(wav), "-af", "silencedetect=noise=-38dB:d=0.15",
                          "-f", "null", "-"], capture_output=True, text=True, check=False).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return [(max(s, 0.0), ends[i] if i < len(ends) else secs) for i, s in enumerate(starts)]


def split_lines(wav, secs, captions):
    """Where each caption line starts and ends inside one scene's take.

    Each boundary between lines goes to the pause nearest where the text length puts it,
    preferring longer pauses (the voice breathes between sentences).
    """
    quiet = silences(wav, secs)
    begin = next((e for s, e in quiet if s <= 0.05), 0.0)
    end = next((s for s, e in quiet if e >= secs - 0.05), secs)
    inner = [(s, e) for s, e in quiet if s > begin + 0.2 and e < end - 0.2]
    sizes = [len(c) for c in captions]
    bounds, used = [], -1
    for k in range(1, len(captions)):
        guess = begin + (end - begin) * sum(sizes[:k]) / sum(sizes)
        options = [(abs((s + e) / 2 - guess) - 0.8 * (e - s), i) for i, (s, e) in enumerate(inner) if i > used]
        if options:
            used = min(options)[1]
            bounds.append(inner[used])
        else:
            bounds.append((guess, guess))
    return list(zip([begin] + [e for _, e in bounds], [s for s, _ in bounds] + [end]))


def timeline():
    """Scene windows, caption cues and the audio to lay down, all in seconds from the start."""
    takes = eleven_takes()
    t, scenes, cues, clips = 0.0, [], [], []
    for index, (name, lines) in enumerate(SCENES):
        start = t
        if takes:  # one ElevenLabs take per scene
            take, at = takes[index], start + EL_LEAD
            secs = wav_secs(take)
            spans = split_lines(take, secs, [caption for caption, _ in lines])
            cues += [{"start": at + a, "end": at + b, "text": caption} for (caption, _), (a, b) in zip(lines, spans)]
            starts = [round(at + a - start, 2) for a, _ in spans]
            clips.append((at, take))
            t = at + secs + EL_TAIL
        else:  # macOS say, one file per line
            cursor, starts = start + LEAD, []
            for caption, spoken in lines:
                wav, secs = speak(spoken or caption)
                cues.append({"start": cursor, "end": cursor + secs, "text": caption})
                clips.append((cursor, wav))
                starts.append(round(cursor - start, 2))
                cursor += secs + GAP
            t = cursor - GAP + TAIL
        if name == "clip":
            t = max(t, start + LEAD + 75 / CLIP_FPS + 0.6)
        scenes.append({"name": name, "start": round(start, 2), "dur": round(t - start, 2), "lines": starts})
    return scenes, cues, clips, round(t, 2), EL_RATE if takes else RATE


def write_narration(clips, total, rate):
    pcm = bytearray(int(total * rate) * 2)
    for at, wav in clips:
        with wave.open(str(wav)) as w:
            assert w.getframerate() == rate and w.getnchannels() == 1 and w.getsampwidth() == 2
            frames = w.readframes(w.getnframes())
        pos = int(at * rate) * 2
        pcm[pos:pos + len(frames)] = frames[:len(pcm) - pos]
    raw = HERE / "narration_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(pcm))
    # One steady speech loudness (about -16 LUFS); single-pass loudnorm also smooths level jumps between takes.
    subprocess.run([str(FFMPEG), "-loglevel", "error", "-y", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", str(rate), str(HERE / "narration.wav")], check=True)


def stamp(sec):
    ms = round(sec * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues):
    blocks = []
    for i, cue in enumerate(cues, 1):
        text = "\n".join(textwrap.wrap(cue["text"], 48))
        blocks.append(f"{i}\n{stamp(cue['start'])} --> {stamp(cue['end'])}\n{text}\n")
    (HERE / "vizagent_demo.srt").write_text("\n".join(blocks), encoding="utf-8")


def prepare_assets():
    ASSETS.mkdir(exist_ok=True)
    sizes = {}
    for key, (shot, crop) in SHOT_SPECS.items():
        if SHOTS:
            Image.open(SHOTS / shot).convert("RGB").crop(crop).save(ASSETS / f"{key}.jpg", quality=93)
        with Image.open(ASSETS / f"{key}.jpg") as img:
            sizes[key] = img.size
    frames = ASSETS / "clip"
    if not (frames / "f075.jpg").exists():
        if not CLIP.exists():
            raise SystemExit(f"street clip not found at {CLIP}; set VIZ_CLIP (see the docstring)")
        frames.mkdir(exist_ok=True)
        subprocess.run([str(FFMPEG), "-loglevel", "error", "-y", "-i", str(CLIP), "-t", "5",
                        "-vf", f"fps={CLIP_FPS},scale=1440:-2", "-q:v", "3", str(frames / "f%03d.jpg")], check=True)
    gsap = HERE / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if gsap.exists():
        shutil.copy(gsap, ASSETS / "gsap.min.js")
    return sizes, sorted(p.name for p in frames.glob("f*.jpg"))


def fit_box(size, max_w, max_h):
    w, h = size
    scale = min(max_w / w, max_h / h)
    return round(w * scale), round(h * scale)


def shot(key, sizes, max_w, max_h, left=None, top=None):
    w, h = fit_box(sizes[key], max_w, max_h)
    left = (1920 - w) // 2 if left is None else left
    top = 160 + (860 - h) // 2 if top is None else top
    return (f'<div class="shot" style="left:{left}px;top:{top}px;width:{w}px;height:{h}px">'
            f'<img src="assets/{key}.jpg" alt=""></div>')


def chip(text):
    return f'<div class="chip"><i></i>{html.escape(text)}</div>'


def team_line():
    names = "".join(f"<span>{html.escape(n)}</span>" for n in TEAM)
    return f'<div class="team"><b>Team 20</b>{names}</div>'


def scene_html(scene, sizes, frames):
    n = scene["name"]
    if n == "title":
        return ('<div class="kicker">VAST Builders Challenge &middot; Real-Time Video Agents</div>'
                '<div class="title">ViZ Agent</div>'
                '<div class="subtitle">Vision Zero conflict finder</div>' + team_line())
    if n == "problem":
        return ('<div class="stat a" style="left:170px;top:210px"><div class="big amber">100</div>'
                '<div class="lbl">fatal + severe crashes<br><b>first half of 2026</b></div></div>'
                '<div class="stat b" style="left:1110px;top:300px"><div class="big dim">92</div>'
                '<div class="lbl">in 2014<br><b>the year SF pledged Vision Zero</b></div></div>'
                '<div class="law"><i></i>Street Safety Act &middot; quarterly public dashboards</div>'
                '<div class="source">Sources: SFMTA report via Streetsblog SF (Aug 2026); Walk SF; KQED</div>')
    if n == "how":
        steps = [("1", "Search", "VAST &middot; NVIDIA VSS"), ("2", "Verify", "YOLO &middot; Cosmos Reason &middot; Nemotron"),
                 ("3", "Rank", "hotspots by camera"), ("4", "Act", "Open311 work order")]
        cards = "".join(f'<div class="card" style="left:{110 + i * 440}px"><div class="step">{k}</div>'
                        f'<div class="ct">{t}</div><div class="cs">{s}</div></div>'
                        for i, (k, t, s) in enumerate(steps))
        arrows = "".join(f'<div class="arrow" style="left:{110 + i * 440 + 392}px">&rsaquo;</div>' for i in range(3))
        return cards + arrows
    if n == "sweep":
        return (chip("Live sweep · 5 cameras") + shot("app", sizes, 1160, 760, left=110, top=190) +
                '<div class="funnel"><div class="fnum dim">60</div><div class="flbl">candidates</div>'
                '<div class="fdown">&darr;</div><div class="fnum cyan">4</div><div class="flbl">verified</div></div>')
    if n == "hot":
        return chip("Where to act first") + shot("hot", sizes, 1700, 640)
    if n == "clip":
        imgs = "".join(f'<img src="assets/clip/{f}" alt="">' for f in frames)
        return chip("Real footage · SF street camera") + f'<div id="clipbook" class="book">{imgs}</div>'
    if n == "second":
        return (f'<div class="still"><img src="assets/clip/{frames[-1]}" alt=""></div>'
                '<div class="look"><div class="lh">Cosmos Reason &middot; second look</div>'
                '<div class="verdict"><span class="letter">B</span><span class="vt">conflict</span></div>'
                '<div class="q q1">&ldquo;outside the designated crosswalk&rdquo;</div>'
                '<div class="q q2">&ldquo;without slowing down or stopping&rdquo;</div></div>')
    if n == "review":
        return chip("Engineer review") + shot("review", sizes, 1500, 800)
    if n == "order":
        return chip("Drafted work order · Open311 · FHWA fix") + shot("order", sizes, 1600, 800)
    if n == "weave":
        return chip("Traced in W&B Weave") + shot("weave", sizes, 1720, 600)
    if n == "close":
        built = "".join(f"<span>{html.escape(x)}</span>" for x in
                        ["VAST Data", "NVIDIA VSS", "Cosmos Reason", "YOLO", "Nemotron", "W&B Weave"])
        return ('<div class="title small">ViZ Agent</div>'
                '<div class="links"><div><b>Live app</b>team-20-app.thecosmoslabs.com/app</div>'
                '<div><b>Code</b>github.com/bhargavchintam/VizAgent</div></div>' + team_line() +
                f'<div class="built">{built}</div>')
    raise ValueError(n)


def scene_js(scene, frames):
    n, s, d, ln = scene["name"], scene["start"], scene["dur"], scene["lines"]
    sel = f"#s-{n}"
    js = [f"tl.fromTo('{sel}', {{opacity: 0}}, {{opacity: 1, duration: 0.45, ease: 'power1.out'}}, {s});",
          f"tl.to('{sel}', {{opacity: 0, duration: 0.35, ease: 'power1.in'}}, {round(s + d - 0.35, 2)});"]

    def at(offset):
        return round(s + offset, 2)

    if n == "title":
        js += [f"tl.from('{sel} .kicker', {{y: 20, opacity: 0, duration: 0.6}}, {at(0.2)});",
               f"tl.from('{sel} .title', {{y: 40, opacity: 0, duration: 0.8, ease: 'power3.out'}}, {at(0.35)});",
               f"tl.from('{sel} .subtitle', {{y: 24, opacity: 0, duration: 0.6}}, {at(0.8)});",
               f"tl.from('{sel} .team > *', {{y: 18, opacity: 0, duration: 0.5, stagger: 0.18}}, {at(ln[1])});"]
    elif n == "problem":
        js += [f"tl.from('{sel} .b', {{y: 30, opacity: 0, duration: 0.6}}, {at(ln[0])});",
               f"tl.from('{sel} .a', {{y: 40, opacity: 0, scale: 0.92, duration: 0.7, ease: 'back.out(1.6)'}}, {at(ln[1])});",
               f"tl.from('{sel} .law', {{y: 24, opacity: 0, duration: 0.6}}, {at(ln[2])});",
               f"tl.from('{sel} .source', {{opacity: 0, duration: 0.6}}, {at(ln[2] + 0.4)});"]
    elif n == "how":
        cards = [ln[1], ln[2], ln[3], ln[3] + 1.4]
        for i, off in enumerate(cards):
            js.append(f"tl.from('{sel} .card:nth-of-type({i + 1})', {{y: 40, opacity: 0, duration: 0.55, ease: 'power2.out'}}, {at(off)});")
            if i:
                js.append(f"tl.from('{sel} .arrow:nth-of-type({i + 4})', {{x: -16, opacity: 0, duration: 0.4}}, {at(off - 0.15)});")
    elif n == "sweep":
        js += [f"tl.from('{sel} .chip', {{x: -30, opacity: 0, duration: 0.5}}, {at(0.2)});",
               f"tl.fromTo('{sel} .shot img', {{scale: 1}}, {{scale: 1.05, duration: {d}, ease: 'none'}}, {s});",
               f"tl.from('{sel} .funnel > :nth-child(-n+2)', {{y: 20, opacity: 0, duration: 0.5, stagger: 0.1}}, {at(ln[0] + 1.2)});",
               f"tl.from('{sel} .fdown', {{y: -20, opacity: 0, duration: 0.4}}, {at(ln[1])});",
               f"tl.from('{sel} .funnel > :nth-child(n+4)', {{scale: 0.6, opacity: 0, duration: 0.6, stagger: 0.1, ease: 'back.out(2)'}}, {at(ln[2])});"]
    elif n == "clip":
        js.append(f"tl.from('{sel} .chip', {{x: -30, opacity: 0, duration: 0.5}}, {at(0.2)});")
        js.append(f"const book = document.querySelectorAll('{sel} .book img');")
        js.append(f"book.forEach((img, i) => {{ tl.set(img, {{opacity: 1}}, {at(LEAD)} + i / {CLIP_FPS}); "
                  f"if (i) tl.set(book[i - 1], {{opacity: 0}}, {at(LEAD)} + i / {CLIP_FPS}); }});")
    elif n == "second":
        js += [f"tl.fromTo('{sel} .still img', {{scale: 1.0}}, {{scale: 1.08, duration: {d}, ease: 'none'}}, {s});",
               f"tl.from('{sel} .lh', {{y: 20, opacity: 0, duration: 0.5}}, {at(0.3)});",
               f"tl.from('{sel} .q1', {{y: 20, opacity: 0, duration: 0.5}}, {at(ln[0] + 2.2)});",
               f"tl.from('{sel} .q2', {{y: 20, opacity: 0, duration: 0.5}}, {at(ln[0] + 3.4)});",
               f"tl.from('{sel} .verdict', {{scale: 0.5, opacity: 0, duration: 0.6, ease: 'back.out(2)'}}, {at(ln[1])});"]
    elif n == "close":
        js += [f"tl.from('{sel} .title', {{y: 30, opacity: 0, duration: 0.7}}, {at(0.2)});",
               f"tl.from('{sel} .links > div', {{y: 20, opacity: 0, duration: 0.5, stagger: 0.2}}, {at(0.8)});",
               f"tl.from('{sel} .team > *', {{y: 16, opacity: 0, duration: 0.45, stagger: 0.15}}, {at(ln[1])});",
               f"tl.from('{sel} .built span', {{y: 14, opacity: 0, duration: 0.4, stagger: 0.08}}, {at(1.4)});"]
    else:  # screenshot scenes
        js += [f"tl.from('{sel} .chip', {{x: -30, opacity: 0, duration: 0.5}}, {at(0.2)});",
               f"tl.from('{sel} .shot', {{y: 30, opacity: 0, duration: 0.6, ease: 'power2.out'}}, {at(0.3)});",
               f"tl.fromTo('{sel} .shot img', {{scale: 1}}, {{scale: 1.04, duration: {d}, ease: 'none'}}, {s});"]
    return "\n      ".join(js)


CSS = """
      * { margin: 0; padding: 0; box-sizing: border-box; }
      html, body { width: 1920px; height: 1080px; background: #0a1020; overflow: hidden; }
      #root { position: relative; width: 1920px; height: 1080px; overflow: hidden; color: #e8eef9;
        font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
        background: radial-gradient(1300px 760px at 72% 18%, #16264a 0%, #0a1020 62%); }
      .scene { position: absolute; inset: 0; }
      .kicker { position: absolute; top: 250px; width: 100%; text-align: center; font-size: 32px; color: #8ea3c7; letter-spacing: 3px; text-transform: uppercase; }
      .title { position: absolute; top: 320px; width: 100%; text-align: center; font-size: 190px; font-weight: 800; letter-spacing: -4px; color: #fff; }
      .title.small { top: 150px; font-size: 140px; }
      .subtitle { position: absolute; top: 560px; width: 100%; text-align: center; font-size: 50px; color: #67e8f9; font-weight: 500; }
      .team { position: absolute; top: 760px; width: 100%; display: flex; justify-content: center; gap: 22px; font-size: 34px; }
      .team > * { padding: 12px 26px; border-radius: 999px; background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12); }
      .team b { background: #22d3ee; color: #06202a; border-color: #22d3ee; }
      #s-close .team { top: 640px; }
      .links { position: absolute; top: 380px; width: 100%; display: flex; flex-direction: column; align-items: center; gap: 22px; font-size: 44px; color: #dbe7ff; }
      .links b { display: inline-block; width: 190px; margin-right: 22px; text-align: right; font-size: 30px; color: #8ea3c7; font-weight: 600; text-transform: uppercase; letter-spacing: 2px; }
      .built { position: absolute; top: 820px; width: 100%; display: flex; justify-content: center; gap: 16px; font-size: 28px; color: #b9c7e3; }
      .built span { padding: 10px 22px; border-radius: 12px; background: rgba(103,232,249,.08); border: 1px solid rgba(103,232,249,.25); }
      .stat { position: absolute; }
      .big { font-size: 330px; font-weight: 800; line-height: 1; letter-spacing: -8px; }
      .stat.b .big { font-size: 220px; }
      .amber { color: #fbbf24; } .dim { color: #64748b; } .cyan { color: #22d3ee; }
      .lbl { margin-top: 18px; font-size: 40px; color: #b9c7e3; line-height: 1.3; }
      .lbl b { color: #fff; }
      .law, .chip { position: absolute; display: inline-flex; align-items: center; gap: 16px; padding: 14px 28px; border-radius: 999px;
        background: rgba(34,211,238,.12); border: 1px solid rgba(34,211,238,.45); color: #a5f3fc; font-size: 34px; font-weight: 600; }
      .law i, .chip i { width: 14px; height: 14px; border-radius: 50%; background: #22d3ee; display: inline-block; }
      .law { left: 170px; top: 800px; }
      .chip { left: 110px; top: 66px; }
      .source { position: absolute; left: 170px; top: 960px; font-size: 24px; color: #64748b; }
      .card { position: absolute; top: 330px; width: 380px; height: 420px; padding: 40px 34px; border-radius: 28px;
        background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)); border: 1px solid rgba(255,255,255,.12); }
      .step { width: 84px; height: 84px; border-radius: 50%; background: #22d3ee; color: #06202a; font-size: 44px; font-weight: 800;
        display: flex; align-items: center; justify-content: center; }
      .ct { margin-top: 46px; font-size: 64px; font-weight: 800; color: #fff; }
      .cs { margin-top: 18px; font-size: 32px; line-height: 1.35; color: #9fb3d6; }
      .arrow { position: absolute; top: 480px; width: 48px; text-align: center; font-size: 90px; color: #22d3ee; line-height: 1; }
      .shot { position: absolute; border-radius: 18px; overflow: hidden; background: #fff;
        box-shadow: 0 30px 90px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.1); }
      .shot img { display: block; width: 100%; height: 100%; object-fit: cover; transform-origin: 50% 40%; }
      .funnel { position: absolute; left: 1350px; top: 240px; width: 460px; text-align: center; }
      .fnum { font-size: 200px; font-weight: 800; line-height: 1; }
      .flbl { font-size: 40px; color: #b9c7e3; margin-top: 4px; }
      .fdown { font-size: 80px; color: #475569; margin: 18px 0; line-height: 1; }
      .book { position: absolute; left: 240px; top: 165px; width: 1440px; height: 810px; border-radius: 18px; overflow: hidden;
        background: #000; box-shadow: 0 30px 90px rgba(0,0,0,.6); }
      .book img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: 0; }
      .still { position: absolute; left: 110px; top: 250px; width: 940px; height: 529px; border-radius: 18px; overflow: hidden;
        box-shadow: 0 30px 90px rgba(0,0,0,.6); }
      .still img { width: 100%; height: 100%; object-fit: cover; filter: brightness(.85); }
      .look { position: absolute; left: 1130px; top: 230px; width: 700px; }
      .lh { font-size: 34px; color: #8ea3c7; font-weight: 600; text-transform: uppercase; letter-spacing: 2px; }
      .verdict { display: flex; align-items: center; gap: 34px; margin: 40px 0 46px; }
      .letter { width: 190px; height: 190px; border-radius: 50%; background: #fbbf24; color: #2a1a00; font-size: 130px; font-weight: 800;
        display: flex; align-items: center; justify-content: center; }
      .vt { font-size: 80px; font-weight: 800; color: #fff; }
      .q { font-size: 40px; font-style: italic; color: #dbe7ff; margin-top: 18px; line-height: 1.3; }
      #brand { position: absolute; left: 110px; top: 1000px; font-size: 24px; color: #5b6b8c; letter-spacing: 1px; }
      #brand b { color: #8ea3c7; }
"""


def build():
    if shutil.which(str(FFMPEG)) is None:
        raise SystemExit("ffmpeg not found: install it (e.g. brew install ffmpeg) or link it into bin/ffmpeg")
    sizes, frames = prepare_assets()
    scenes, cues, clips, total, rate = timeline()
    write_narration(clips, total, rate)
    write_srt(cues)
    gsap_src = "assets/gsap.min.js" if (ASSETS / "gsap.min.js").exists() else \
        "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
    body = []
    for i, sc in enumerate(scenes):
        body.append(f'    <div id="s-{sc["name"]}" class="clip scene" data-start="{sc["start"]}" '
                    f'data-duration="{sc["dur"]}" data-track-index="1">{scene_html(sc, sizes, frames)}</div>')
    first, last = scenes[1]["start"], scenes[-1]["start"]
    body.append(f'    <div id="brand" class="clip" data-start="{first}" data-duration="{round(last - first, 2)}" '
                f'data-track-index="2"><b>ViZ Agent</b> &middot; Team 20 &middot; VAST Builders Challenge</div>')
    body.append(f'    <audio id="narration" src="narration.wav" data-start="0" data-duration="{total}" '
                f'data-track-index="0" data-volume="1"></audio>')
    js = "\n      ".join(scene_js(sc, frames) for sc in scenes)
    page = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=1920, height=1080">
    <title>ViZ Agent demo</title>
    <script src="{gsap_src}"></script>
    <style>{CSS}    </style>
  </head>
  <body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="1920" data-height="1080" data-fps="30">
{chr(10).join(body)}
  </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      {js}
      window.__timelines = window.__timelines || {{}};
      window.__timelines.main = tl;
    </script>
  </body>
</html>
"""
    (HERE / "index.html").write_text(page, encoding="utf-8")
    for sc in scenes:
        print(f"{sc['start']:7.2f}s  {sc['dur']:5.2f}s  {sc['name']}")
    print(f"total {total}s, {len(cues)} captions, voice: {'ElevenLabs v4' if rate == EL_RATE else 'macOS say'}")


if __name__ == "__main__":
    build()
