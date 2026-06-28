# JB Visual Audio

A minimap radar for Jailbreak. A detection process reads the in-game minimap
from the screen and streams the local player's team, heading, and nearby enemy
positions to a separate overlay process, which draws a translucent,
click-through directional ring on top of the game.

## Architecture

The two processes run independently and communicate over a local UDP socket
(`127.0.0.1:1780`):

| Process         | Role                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `detection.py`  | Captures the minimap region, detects team / heading / enemy markers, and broadcasts state as JSON over UDP. Shows a debug window. |
| `overlay.py`    | Receives state and renders a frameless, always-on-top, click-through directional overlay. **Windows only.** |
| `config.py`     | Shared settings (UDP endpoint, team colours) imported by both.       |

## Requirements

- Python 3.10+
- The packages in `requirements.txt`
- Windows is required for `overlay.py` (it uses `pywin32` for click-through).
  `detection.py` itself is cross-platform.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Start the overlay first, then the detector, in two terminals:

```bash
python overlay.py
python detection.py
```

Press `q` in the detection debug window to stop it.

## Configuration

- **UDP endpoint and team colours** — `config.py`.
- **Capture region** — `P1` / `P2` in `detection.py` define the minimap's
  screen-space corners; adjust these to match your resolution and HUD layout.
- **Detection tuning** — colour tolerances, heading-jump filtering, and marker
  scan confidence are the constants near the top of `detection.py`.
- **Overlay appearance** — ring radius, arc spans, fade, and pop-up timing are
  the constants near the top of `overlay.py`.

The detection debug window is positioned at `x=1920` (a second monitor) via
`cv2.moveWindow` in `detection.py`; change this if you run a single display.

## Reference images

`Images/` holds the marker and arrow templates used for matching. Only
`playerMarker.png` is currently loaded (as the enemy-marker template); the
others are kept for reference and tuning.
