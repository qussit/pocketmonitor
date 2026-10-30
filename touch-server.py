#!/usr/bin/env python3
"""Сервер второго монитора: раздаёт страницу (видео + касания) и инжектит
касания с телефона в Hyprland через виртуальную мышь (uinput-inject).

Два режима (env MODE):
  gaming  — H.264 (NVENC) в fragmented MP4, почти 0% CPU (для игр).
  latency — MJPEG, минимальная задержка (для видео со звуком).

Видео: читает поток из FIFO (VIDEO_FIFO) и раздаёт клиентам по HTTP.
Тач (WebSocket /ws) — тот же в обоих режимах.
"""
import asyncio
import json
import os
import queue
import subprocess
import threading
import time

from aiohttp import web, WSMsgType

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".local", "share", "second-monitor")
INDEX = os.path.join(DIR, "index.html")
UINPUT = os.path.expanduser("~/.local/bin/uinput-inject")
PORT = int(os.environ.get("TOUCH_PORT", "9000"))
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")

MODE = os.environ.get("MODE", "gaming")
VIDEO_FIFO = os.environ.get(
    "VIDEO_FIFO", os.path.join(RUN, "second-monitor", "video.fifo"))
if MODE == "latency":
    VIDEO_CT = "multipart/x-mixed-replace; boundary=ffmpeg"
    CODEC = ""
else:
    VIDEO_CT = "video/mp4"
    # constrained baseline, уровень до 5.1 — покрывает наш NVENC-поток
    CODEC = "avc1.42E033"


# --- геометрия виртуального монитора (кэш) ---
_geom = None
_geom_ts = 0.0


def hyprctl(*args):
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True).stdout


def _get_geom_sync():
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


async def get_geom():
    if _geom is not None and time.time() - _geom_ts <= 2.0:
        return _geom
    return await asyncio.to_thread(_get_geom_sync)


def _cursorpos_sync():
    try:
        x, y = hyprctl("cursorpos").strip().split(",")
        return float(x), float(y)
    except Exception:
        return 0.0, 0.0


async def cursorpos():
    return await asyncio.to_thread(_cursorpos_sync)


# --- инжектор (виртуальная мышь) — отдельный поток, не блокирует event loop ---
injector = None
_inj_q = queue.Queue()
_inj_lock = threading.Lock()


def _start_injector():
    global injector
    injector = subprocess.Popen([UINPUT], stdin=subprocess.PIPE, text=True, bufsize=1)


def _injector_worker():
    while True:
        cmd = _inj_q.get()
        if cmd is None:
            break
        with _inj_lock:
            try:
                if injector is None or injector.poll() is not None:
                    _start_injector()
                injector.stdin.write(cmd + "\n")
                injector.stdin.flush()
            except (BrokenPipeError, OSError):
                _start_injector()


def inject(cmd):
    _inj_q.put_nowait(cmd)


async def handle_touch(ev):
    g = await get_geom()
    if not g:
        return
    t = ev.get("t")
    s = g["scale"]
    if t in ("d", "m", "r"):
        tx = g["x"] + ev["x"] * g["w"] / s
        ty = g["y"] + ev["y"] * g["h"] / s
        cx, cy = await cursorpos()
        inject("m %d %d" % (round(tx - cx), round(ty - cy)))
        if t == "d":
            inject("b l 1")
        elif t == "r":
            inject("b r 1")
            await asyncio.sleep(0.03)
            inject("b r 0")
    elif t == "u":
        inject("b l 0")
    elif t == "s":
        inject("w %d %d" % (round(ev.get("dy", 0)), round(ev.get("dx", 0))))


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


# --- видео-ретрансляция (FIFO -> клиенты) ---
_clients = set()  # asyncio.Queue на клиента
_bcast_task = None
_init = b""  # init-сегмент fMP4 (ftyp+moov), кэш для gaming-режима


def _box_iter(buf):
    i = 0
    n = len(buf)
    while i + 8 <= n:
        size = int.from_bytes(buf[i:i + 4], "big")
        btype = buf[i + 4:i + 8].decode("latin1", "ignore")
        hdr = 8
        if size == 1:
            if i + 16 > n:
                break
            size = int.from_bytes(buf[i + 8:i + 16], "big")
            hdr = 16
        if size == 0:  # «до конца файла» — не парсим дальше
            break
        end = i + size
        if end > n:
            break
        yield (btype, i, end)
        i = end


def _extract_init(buf):
    boxes = list(_box_iter(buf))
    if len(boxes) >= 2 and boxes[0][0] == "ftyp" and boxes[1][0] == "moov":
        return bytes(buf[:boxes[1][2]]), buf[boxes[1][2]:]
    return None, buf


def _extract_fragments(buf):
    boxes = list(_box_iter(buf))
    frags = []
    i = 0
    last = 0
    while i + 1 < len(boxes):
        if boxes[i][0] == "moof" and boxes[i + 1][0] == "mdat":
            frags.append(bytes(buf[boxes[i][1]:boxes[i + 1][2]]))
            last = boxes[i + 1][2]
            i += 2
        else:
            break
    return frags, buf[last:]


def _push(data):
    for q in list(_clients):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(data)
            except Exception:
                pass


async def _broadcaster():
    global _init
    buf = bytearray()
    fd = -1
    while True:
        if fd < 0:
            try:
                fd = os.open(VIDEO_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                await asyncio.sleep(0.5)
                continue
        got = False
        try:
            chunk = os.read(fd, 262144)
            if chunk:
                buf += chunk
                got = True
            else:  # EOF: писатель исчез
                os.close(fd)
                fd = -1
                await asyncio.sleep(0.5)
                continue
        except BlockingIOError:
            pass
        except OSError:
            os.close(fd)
            fd = -1
            await asyncio.sleep(0.5)
            continue

        if MODE == "latency":
            # MJPEG: просто пересылаем байты как есть
            if buf:
                _push(bytes(buf))
                buf.clear()
        else:
            # fMP4: кэшируем init, пересылаем целые фрагменты
            if not _init:
                init, buf = _extract_init(buf)
                if init is not None:
                    _init = init
            frags, buf = _extract_fragments(buf)
            for f in frags:
                _push(f)

        if len(buf) > 4 * 1024 * 1024:
            buf = buf[-1024 * 1024:]

        if not got:
            await asyncio.sleep(0.01)


async def video_handler(request):
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = VIDEO_CT
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "close"
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=16)
    _clients.add(q)
    try:
        if MODE != "latency" and _init:
            await resp.write(_init)
        while True:
            data = await q.get()
            await resp.write(data)
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        _clients.discard(q)
    return resp


async def mode_handler(_req):
    return web.json_response({"mode": MODE, "codec": CODEC, "video": "/video"})


async def index_handler(_req):
    return web.FileResponse(INDEX)


def _static(name, ctype):
    async def handler(_req):
        return web.FileResponse(os.path.join(DIR, name), headers={"Content-Type": ctype})
    return handler


def main():
    _start_injector()
    threading.Thread(target=_injector_worker, daemon=True).start()
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/video", video_handler)
    app.router.add_get("/video.mjpg", video_handler)  # совместимость
    app.router.add_get("/mode", mode_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/manifest.json", _static("manifest.json", "application/manifest+json"))
    app.router.add_get("/sw.js", _static("sw.js", "application/javascript"))
    app.router.add_get("/icon-192.png", _static("icon-192.png", "image/png"))
    app.router.add_get("/icon-512.png", _static("icon-512.png", "image/png"))
    app.on_startup.append(_start_broadcaster)
    print(f"[touch] mode={MODE} serving http://127.0.0.1:{PORT} (video: /video)")
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


async def _start_broadcaster(_app):
    global _bcast_task
    if _bcast_task is None:
        _bcast_task = asyncio.create_task(_broadcaster())


if __name__ == "__main__":
    main()
