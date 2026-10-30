#!/usr/bin/env python3
"""Сервер второго монитора: раздаёт страницу (видео MJPEG + касания) и
инжектит касания с телефона в Hyprland через виртуальную мышь (uinput-inject).

Видео: читает MJPEG-поток из FIFO (его пишет wf-recorder --muxer=mpjpeg)
и раздаёт клиентам по HTTP как multipart/x-mixed-replace (для <img>).

Тач (WebSocket /ws):
  {"t":"d","x":nx,"y":ny}   — касание началось (норм. 0..1)
  {"t":"m","x":nx,"y":ny}   — палец двигается
  {"t":"u"}                 — палец поднят
  {"t":"s","dx":px,"dy":py} — прокрутка двумя пальцами (пиксели)
  {"t":"r","x":nx,"y":ny}   — правый клик (тап двумя пальцами)
"""
import asyncio
import json
import os
import subprocess
import time

from aiohttp import web, WSMsgType

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".local", "share", "second-monitor")
INDEX = os.path.join(DIR, "index.html")
UINPUT = os.path.expanduser("~/.local/bin/uinput-inject")
PORT = int(os.environ.get("TOUCH_PORT", "9000"))
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
MJPEG_FIFO = os.environ.get("MJPEG_FIFO", os.path.join(RUN, "second-monitor", "mjpeg.fifo"))

# --- геометрия виртуального монитора (кэш) ---
_geom = None
_geom_ts = 0.0


def hyprctl(*args):
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True).stdout


def get_geom():
    global _geom, _geom_ts
    now = time.time()
    if _geom is None or now - _geom_ts > 2.0:
        try:
            for m in json.loads(hyprctl("monitors", "-j")):
                if m["name"].startswith("HEADLESS"):
                    _geom = {
                        "x": m["x"], "y": m["y"],
                        "w": m["width"], "h": m["height"],
                        "scale": m.get("scale", 1) or 1,
                    }
                    break
        except Exception:
            pass
        _geom_ts = now
    return _geom


def cursorpos():
    try:
        x, y = hyprctl("cursorpos").strip().split(",")
        return float(x), float(y)
    except Exception:
        return 0.0, 0.0


# --- инжектор (виртуальная мышь) ---
injector = None
_inj_lock = asyncio.Lock()


def start_injector():
    global injector
    injector = subprocess.Popen([UINPUT], stdin=subprocess.PIPE, text=True, bufsize=1)


async def inject(cmd):
    async with _inj_lock:
        if injector is None or injector.poll() is not None:
            start_injector()
        try:
            injector.stdin.write(cmd + "\n")
            injector.stdin.flush()
        except (BrokenPipeError, OSError):
            start_injector()


async def handle_touch(ev):
    g = get_geom()
    if not g:
        return
    t = ev.get("t")
    s = g["scale"]
    if t in ("d", "m", "r"):
        tx = g["x"] + ev["x"] * g["w"] / s
        ty = g["y"] + ev["y"] * g["h"] / s
        cx, cy = cursorpos()
        await inject("m %d %d" % (round(tx - cx), round(ty - cy)))
        if t == "d":
            await inject("b l 1")
        elif t == "r":
            await inject("b r 1")
            await asyncio.sleep(0.03)
            await inject("b r 0")
    elif t == "u":
        await inject("b l 0")
    elif t == "s":
        await inject("w %d %d" % (round(ev.get("dy", 0)), round(ev.get("dx", 0))))


# --- WebSocket ---
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            await handle_touch(json.loads(msg.data))
        except Exception as e:
            print("touch error:", e)
    return ws


# --- MJPEG-ретрансляция ---
_clients = set()  # asyncio.Queue на клиента
_bcast_task = None


async def _broadcaster():
    loop = asyncio.get_event_loop()
    fd = -1
    while True:
        if fd < 0:
            try:
                fd = os.open(MJPEG_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                await asyncio.sleep(0.5)
                continue
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        except OSError:
            os.close(fd)
            fd = -1
            await asyncio.sleep(0.5)
            continue
        if not data:
            # writer умер (EOF) — переоткрываем
            os.close(fd)
            fd = -1
            await asyncio.sleep(0.5)
            continue
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    pass


async def mjpg_handler(request):
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "multipart/x-mixed-replace; boundary=ffmpeg"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "close"
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=8)
    _clients.add(q)
    try:
        while True:
            data = await q.get()
            await resp.write(data)
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        _clients.discard(q)
    return resp


async def index_handler(_req):
    return web.FileResponse(INDEX)


def _static(name, ctype):
    async def handler(_req):
        return web.FileResponse(os.path.join(DIR, name), headers={"Content-Type": ctype})
    return handler


def main():
    start_injector()
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/video.mjpg", mjpg_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/manifest.json", _static("manifest.json", "application/manifest+json"))
    app.router.add_get("/sw.js", _static("sw.js", "application/javascript"))
    app.router.add_get("/icon-192.png", _static("icon-192.png", "image/png"))
    app.router.add_get("/icon-512.png", _static("icon-512.png", "image/png"))
    app.on_startup.append(_start_broadcaster)
    print(f"[touch] serving http://127.0.0.1:{PORT} (mjpeg: /video.mjpg, ws: /ws)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


async def _start_broadcaster(_app):
    global _bcast_task
    if _bcast_task is None:
        _bcast_task = asyncio.create_task(_broadcaster())


if __name__ == "__main__":
    main()
