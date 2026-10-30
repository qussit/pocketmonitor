# second-monitor

Use an Android phone as a **second monitor** for your Hyprland (Wayland) desktop, over USB — with working **touch input**.

- **Video**: the phone's browser shows an MJPEG stream — universally decodable, low latency.
- **Touch**: taps and drags are injected into Hyprland through a virtual mouse (`uinput`), so you can actually click, drag and scroll windows on the phone screen.
- **No root** on the phone; the phone only needs a browser (and USB debugging for the USB tunnel).

```
Hyprland headless output ──► wf-recorder (MJPEG) ──► FIFO ──► touch-server.py (HTTP)
                                                              │  /video.mjpg ──► phone <img>
                                                              └─ /ws (touch) ──► uinput ──► Hyprland
```

## How it works

1. A virtual (headless) monitor is created in Hyprland and placed below the real monitor(s).
2. `wf-recorder` captures that output and encodes it to MJPEG into a FIFO.
3. `touch-server.py` (Python + aiohttp) serves a web page to the phone: the MJPEG stream, plus a WebSocket that receives touch events.
4. Touch events are turned into mouse moves/clicks and injected by `uinput-inject` (a tiny C program that creates a virtual mouse via `/dev/uinput`).
5. `adb reverse` tunnels the page to the phone over USB, so the phone just opens `http://localhost:9000/`.

## Requirements

- Hyprland (Wayland) with `hyprctl`
- `wf-recorder`, `jq`, `adb` (`android-tools`), `python3` + `aiohttp`, `gcc`
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
   second-monitor start
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
| `MJQ` | `6` | MJPEG quality (2–31, lower = better) |
| `POS` | `below` | position: `below`, `auto`, or e.g. `1920x0` |

Examples:

```sh
RES=1080x2460@60 POS=1920x0 second-monitor start   # portrait, on the right
SCALE=3 second-monitor start                        # larger UI
```

## Autostart (Hyprland)

```sh
cp hyprland/second-monitor.conf ~/.config/hypr/config/
# then add to ~/.config/hypr/hyprland.conf:
#   source = ~/.config/hypr/config/second-monitor.conf
```

The bundled `daemon` mode also re-applies `adb reverse` automatically when you
plug the phone in, and exits when Hyprland quits.

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

- Video is MJPEG (higher bandwidth than H.264, but decodes anywhere and is low-latency).
- No audio.
- Touch is a virtual mouse (relative pointer via `uinput`), not a native touchscreen.
- Tested on Hyprland 0.5x + NVIDIA; other Wayland compositors aren't supported (the headless-output creation is Hyprland-specific).

## License

[MIT](LICENSE)
