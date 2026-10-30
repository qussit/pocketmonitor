# second-monitor

Use an Android phone as a **second monitor** for your Hyprland (Wayland) desktop, over USB — with working **touch input**.

- **Two encoding modes** (switchable):
  - `gaming` — H.264 via NVENC (hardware, ~0% CPU), for when you don't want to steal CPU from games.
  - `latency` — MJPEG, minimal delay, for watching video with synced audio.
- **Touch**: taps and drags are injected into Hyprland through a virtual mouse (`uinput`) — click, drag and scroll on the phone screen.
- **No root** on the phone; it only needs a browser (and USB debugging for the USB tunnel).

```
Hyprland headless output ──► wf-recorder (H.264/MJPEG) ──► FIFO ──► touch-server.py (HTTP)
                                                                   │  /video ──► phone (MSE / <img>)
                                                                   └─ /ws (touch) ──► uinput ──► Hyprland
```

## How it works

1. A virtual (headless) monitor is created in Hyprland and placed below the real monitor(s).
2. `wf-recorder` captures it and encodes to H.264 (NVENC) or MJPEG into a FIFO.
3. `touch-server.py` (Python + aiohttp) serves a web page to the phone: the video stream plus a WebSocket that receives touch events.
4. Touch events become mouse moves/clicks, injected by `uinput-inject` (a tiny C program that creates a virtual mouse via `/dev/uinput`).
5. `adb reverse` tunnels the page to the phone over USB, so the phone just opens `http://localhost:9000/`.

## Modes

| Mode | Encoding | CPU | Latency | Use for |
|---|---|---|---|---|
| `gaming` (default) | H.264 NVENC (GPU) | ~1–2% | ~300 ms | games, general use |
| `latency` | MJPEG (software) | high | ~50 ms | video with synced audio |

```sh
second-monitor start            # gaming (default)
second-monitor start latency    # minimal latency
```

## Requirements

- Hyprland (Wayland) with `hyprctl`
- `wf-recorder`, `ffmpeg`, `jq`, `adb` (`android-tools`), `python3` + `aiohttp`, `gcc`
- Android phone with USB debugging enabled
- Write access to `/dev/uinput` (only needed for touch; video works without it)

## Install

```sh
git clone https://github.com/<user>/second-monitor.git
cd second-monitor
./install.sh
```

`install.sh` installs dependencies, builds `uinput-inject`, and copies files to
`~/.local/bin` and `~/.local/share/second-monitor`.

## Usage

1. On the phone: **Developer options → USB debugging**, plug in the cable, authorize the PC.
2. Start it:
   ```sh
   second-monitor start            # gaming mode
   second-monitor start latency    # low-latency mode
   ```
3. On the phone, open Chrome and go to **`http://localhost:9000/`** (tap once to enter fullscreen).
4. Menu ⋮ → **Add to Home Screen** to install it as a fullscreen app.

Stop with `second-monitor stop`, check state with `second-monitor status`.

## Touch gestures

| Gesture | Action |
|---|---|
| Tap | left click |
| Drag | move / click-drag |
| Two-finger drag | scroll |
| Two-finger tap | right click |

## Configuration

Environment variables (defaults in parentheses):

| Variable | Default | Meaning |
|---|---|---|
| `RES` | `2460x1080@60` | virtual monitor resolution (matches Infinix Hot 30) |
| `SCALE` | `2` | UI scale (bigger = larger UI) |
| `FPS` | `30` | stream frame rate |
| `MJQ` | `6` | MJPEG quality (2–31, lower = better) — `latency` mode only |
| `POS` | `below` | position: `below`, `auto`, or e.g. `1920x0` |

Examples:

```sh
RES=1080x2460@60 POS=1920x0 second-monitor start   # portrait, on the right
SCALE=3 second-monitor start                        # larger UI
FPS=15 second-monitor start                         # even less CPU in gaming mode
```

## Autostart (Hyprland)

```sh
cp hyprland/second-monitor.conf ~/.config/hypr/config/
# then add to ~/.config/hypr/hyprland.conf:
#   source = ~/.config/hypr/config/second-monitor.conf
```

The bundled `daemon` mode starts in `gaming` mode, re-applies `adb reverse`
automatically when you plug the phone in, and exits when Hyprland quits. To
autostart in `latency` mode instead, change the command to
`second-monitor daemon latency`.

## `/dev/uinput` access (for touch)

If your user can't write to `/dev/uinput`, video still works but touch won't.
Grant access with an ACL (easiest):

```sh
sudo setfacl -m u:$USER:rw /dev/uinput
```

…or a udev rule (persistent):

```sh
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload && sudo udevadm trigger
sudo usermod -aG input "$USER"   # then re-login
```

## Limitations

- `latency` mode (MJPEG) is CPU-heavy — it will compete with games; use `gaming` mode then.
- `gaming` mode has ~300 ms latency (fine for a second monitor, not for A/V-synced video).
- No audio.
- Touch is a virtual mouse (relative pointer via `uinput`), not a native touchscreen.
- Tested on Hyprland 0.5x + NVIDIA; other Wayland compositors aren't supported (headless-output creation is Hyprland-specific).

## License

[MIT](LICENSE)
