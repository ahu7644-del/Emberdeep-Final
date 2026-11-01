# "Whaddup My Dawg": a pixel-art TikTok skit (9:16, 0:32)

**Output:** `out/whaddup_my_dawg.mp4` (1080x1920, 30 fps, -14 LUFS), cover: `out/cover.png`

## The idea

The "dawg" family of TikTok memes runs on dog/"dawg" wordplay, bro-talk aimed at things that are not bros,
and a fast absurd payoff. This is an original skit in that spirit. It doesn't reuse any existing clip, audio or
character.

**Biscuit**, a chill dog in shades and a gold chain, greets *everything* as his dawg. A scoreboard keeps count.

| Time | Beat | The joke |
|---|---|---|
| 0:00 | Close-up, to camera: *"Whaddup my dawg."* | Hook text: **MY DOG GREETS EVERYTHING LIKE THIS** |
| 0:02 | The mailman | *"Not today, Biscuit!"* He sprints off-screen in a cloud of dust |
| 0:05 | A squirrel (chipmunk voice) | *"Bro. I'm literally a squirrel."* An acorn hits him: **BONK!** Biscuit, dazed: *"Love you too, dawg."* |
| 0:09 | A cat (posh British) | *"I am a cat."* / *"Dawg is a state of mind."* / *"...Ugh."* The cat turns its back |
| 0:16 | A sidewalk delivery robot | *"Whaddup my—"* Record scratch, the music dies, red vignette, glasses slide down. **NOT MY DAWG.** *"Nah. Not you. You know what you did."* The robot beeps and flees. **ENEMIES: 1** |
| 0:21 | His reflection in a puddle | Both say *"Whaddup my dawg!"* in echo. *"Finally. Someone who gets it."* Hearts. |
| 0:25 | A hot dog on the sidewalk | God rays, angel choir. Biscuit takes his shades off out of respect: *"Whaddup... my dawg."* Pause. **CHOMP.** **R.I.P. MY DAWG** as the hot dog's ghost floats up with a halo, plus a sad violin: *"You will be missed."* **\*BURP\*** **DAWGS EATEN: 1** |
| 0:30 | Back to the close-up | "FOLLOW IF YOU'RE MY DAWG". It loops into the opening line |

Little touches: the mouths lip-sync to each voice, the robot's antenna flag hangs limp (a nod to the
no-wind video), and a lo-fi boom-bap beat runs under it all and cuts out for the gags.

## Suggested post copy

**Caption:** he has never met a stranger (except one) 🐶🕶️

**Hashtags:** `#whaddupmydawg #mydawg #dawg #dogsoftiktok #pixelart #animation #funny #meme #dog #fyp`

**Cover:** the close-up at 0:00 with the hook text.

## Re-rendering

This reuses the toolkit in `../no-wind` (pixel art, audio and text helpers) and the same Kokoro voice model.
See that README for setup. Then:

```bash
python make_dawg.py               # -> out/whaddup_my_dawg.mp4 (~40 s)
python make_dawg.py --preview 0.8 # contact sheet -> build/preview.png
```

Voices (Kokoro-82M): Biscuit `am_michael` pitched down, mailman `am_adam`, squirrel `am_puck` pitched way up, cat `bm_george`.
To recast, edit `SPK` at the top of `make_dawg.py`. Lines live in `LINES`.
