# -*- coding: utf-8 -*-
"""
server.py — Android Studio 后端

浏览器 ── /api/screen | /api/stream | /api/action | /api/run | /api/logs (SSE)
   │
FastAPI (server.py)
   ├─ Device     · io_lock 串行 · 所有操作走 adb shell · 帧缓存 · 动作优先
   ├─ 脚本执行   · 线程 exec + ScriptDevice API + PyThreadState_SetAsyncExc 强制中断
   └─ 日志       · deque + SSE + 按 key 限流
   │
   adb (platform-tools) ── USB/网络 ── Android 设备

对比 iPhone-Studio：
- 不需要 WDA：Android 原生 `input` 命令即触控/按键，`uiautomator dump` 即元素树。
- 不需要端口转发：adb exec-out 直接取数据。
- 坐标单位是物理像素(px)，由 `wm size` 实时读取。
"""
import argparse
import io
import json
import os
import re
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

import ctl

BASE = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE / "scripts"
SHOTS_DIR = BASE / "shots"
DEMO_DIR = BASE / "demo"
for d in (SCRIPTS_DIR, SHOTS_DIR, DEMO_DIR):
    d.mkdir(exist_ok=True)

# 启动时清除环境代理，避免 requests 被系统代理劫持
for k in list(os.environ):
    if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

app = FastAPI(title="Android Studio")


def log(msg, key=None):
    """写日志（SSE 推送），key 相同的 5 秒内只刷一次，避免刷屏。"""
    LOGBUS.push(msg, key)


class LogBus:
    def __init__(self):
        self.buf = deque(maxlen=500)
        self.lock = threading.Lock()
        self._last_by_key = {}
        self.version = 0

    def push(self, msg, key=None):
        now = time.time()
        with self.lock:
            if key:
                if now - self._last_by_key.get(key, 0) < 5:
                    return
                self._last_by_key[key] = now
            line = f"[{time.strftime('%H:%M:%S')}] {msg}"
            self.buf.append(line)
            self.version += 1

    def snapshot(self):
        with self.lock:
            return list(self.buf)

    def since(self, version):
        with self.lock:
            if version == self.version:
                return self.version, []
            lines = list(self.buf)
            return self.version, lines


LOGBUS = LogBus()


# ---------------------------------------------------------------------------
# Device：所有 adb 操作的唯一出口（io_lock 串行化）
# ---------------------------------------------------------------------------

class Device:
    """封装对单台 Android 设备的全部操作。

    关键设计：
    - io_lock：adb 命令一次只跑一条，避免并发互相踩（尤其 screencap 与 input）。
    - 逻辑尺寸(px)每次实时读取，绝不缓存（应用可能改分辨率/旋转）。
    - screenshot() 帧缓存：同一秒内的多次请求共用同一帧。
    """

    def __init__(self):
        self.io_lock = threading.Lock()
        self.frame_lock = threading.Lock()
        self._serial = None
        self._frame = None            # (jpeg_bytes, w, h, ts)
        self._action_count = 0        # 动作计数，抓帧让路用
        self.demo = True
        self._adb_kb = None           # ADBKeyboard 是否可用（None=未探测）

    # ---------------- 基础通道 ----------------

    def _serial_or_raise(self):
        if self.demo:
            raise RuntimeError("演示模式下没有真实设备")
        s = self._serial
        if s and ctl.first_serial() == s:
            return s
        s = ctl.first_serial()
        if not s:
            raise RuntimeError("没有在线设备")
        self._serial = s
        return s

    def shell(self, cmd, timeout=20):
        with self.io_lock:
            return ctl.shell(cmd, timeout=timeout, serial=self._serial_or_raise())

    def exec_out(self, cmd, timeout=25):
        with self.io_lock:
            return ctl.exec_out(cmd, timeout=timeout, serial=self._serial_or_raise())

    def _begin_action(self):
        self._action_count += 1

    def _end_action(self):
        self._action_count -= 1

    # ---------------- 状态 ----------------

    def refresh(self):
        """探测设备，返回是否在线。"""
        s = ctl.first_serial()
        if s:
            was_demo = self.demo
            self.demo = False
            self._serial = s
            if was_demo:
                log(f"设备上线: {s}")
            return True
        if not self.demo:
            log("设备掉线，进入演示模式")
        self.demo = True
        self._serial = None
        self._adb_kb = None
        return False

    def info(self):
        if self.demo:
            return {"online": False, "demo": True}
        s = self._serial_or_raise()
        try:
            with self.io_lock:
                size_txt = ctl.shell("wm size", serial=s)
                dens_txt = ctl.shell("wm density", serial=s)
                ver = ctl.shell("getprop ro.build.version.release", serial=s).strip()
                sdk = ctl.shell("getprop ro.build.version.sdk", serial=s).strip()
                model = ctl.shell("getprop ro.product.model", serial=s).strip()
                brand = ctl.shell("getprop ro.product.brand", serial=s).strip()
                battery = ctl.shell("dumpsys battery | grep level", serial=s).strip()
            w, h = ctl.parse_wm_size(size_txt)
            return {
                "online": True,
                "demo": False,
                "serial": s,
                "model": f"{brand} {model}".strip(),
                "android": ver,
                "sdk": sdk,
                "size": [w, h],
                "density": ctl.parse_wm_density(dens_txt),
                "battery": re.sub(r"\D", "", battery) or "?",
            }
        except Exception as e:
            log(f"读取设备信息失败: {e}", key="info")
            self.demo = True
            return {"online": False, "demo": True, "error": str(e)}

    def window_size(self):
        """实时读取屏幕逻辑尺寸 (w, h)，单位 px。"""
        if self.demo:
            return (1080, 2340)
        txt = self.shell("wm size")
        w, h = ctl.parse_wm_size(txt)
        if not w:
            raise RuntimeError("wm size 解析失败")
        return (w, h)

    def rotation(self):
        """返回当前显示旋转 (0/1/2/3)。"""
        if self.demo:
            return 0
        try:
            txt = self.shell("dumpsys display | grep -E 'mDisplayRotation|mRotation'")
            return ctl.parse_rotation(txt)
        except Exception:
            return 0

    def screen_on(self):
        if self.demo:
            return True
        try:
            return "mWakefulness=Awake" in self.shell("dumpsys power | grep mWakefulness=")
        except Exception:
            return True

    # ---------------- 画面 ----------------

    def screenshot_jpeg(self, quality=70):
        """抓一帧屏幕并编码为 JPEG。带帧缓存；有动作时沿用上一帧。"""
        if self.demo:
            return self._demo_frame()
        now = time.time()
        with self.frame_lock:
            if self._frame and now - self._frame[3] < 0.35:
                return self._frame[0], self._frame[1], self._frame[2], True
        # 动作优先：有触控/按键在排队时抓帧主动让路
        if self._action_count > 0:
            if self._frame:
                return self._frame[0], self._frame[1], self._frame[2], True
        png = self.exec_out("screencap -p", timeout=25)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = img.size
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        jpeg = buf.getvalue()
        with self.frame_lock:
            self._frame = (jpeg, w, h, time.time())
        return jpeg, w, h, False

    def _demo_frame(self):
        cache = DEMO_DIR / "demo.jpg"
        if not cache.exists():
            img = Image.new("RGB", (1080, 2340), (18, 22, 30))
            dr = ImageDraw.Draw(img)
            dr.text((400, 1100), "DEMO MODE", fill=(120, 140, 180))
            dr.text((330, 1160), "Android Studio 演示画面", fill=(160, 170, 200))
            dr.text((300, 1220), "USB 连接设备后自动进入实况", fill=(110, 120, 150))
            img.save(cache, "JPEG", quality=80)
        img = Image.open(cache)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        b = buf.getvalue()
        return b, img.size[0], img.size[1], True

    # ---------------- 触控 / 按键 ----------------

    def tap(self, x, y):
        self._begin_action()
        try:
            self.shell(f"input tap {int(x)} {int(y)}", timeout=10)
        finally:
            self._end_action()

    def double_tap(self, x, y):
        self._begin_action()
        try:
            # Android 原生没有 double tap 输入，用两次快速 tap 模拟
            self.shell(f"input tap {int(x)} {int(y)}", timeout=10)
            time.sleep(0.08)
            self.shell(f"input tap {int(x)} {int(y)}", timeout=10)
        finally:
            self._end_action()

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        self._begin_action()
        try:
            self.shell(
                f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration*1000)}",
                timeout=15,
            )
        finally:
            self._end_action()

    def swipe_dir(self, direction, duration=0.35, margin=0.22):
        w, h = self.window_size()
        margin = max(0.05, min(0.45, margin))
        cx = w / 2
        cy = h / 2
        m = {
            "up":    (cx, h * (1 - margin), cx, h * margin),
            "down":  (cx, h * margin, cx, h * (1 - margin)),
            "left":  (w * (1 - margin), cy, w * margin, cy),
            "right": (w * margin, cy, w * (1 - margin), cy),
        }
        if direction not in m:
            raise ValueError("direction 只能是 up/down/left/right")
        x1, y1, x2, y2 = m[direction]
        self.swipe(x1, y1, x2, y2, duration)

    def key(self, keycode):
        self._begin_action()
        try:
            self.shell(f"input keyevent {int(keycode)}", timeout=10)
        finally:
            self._end_action()

    def home(self):
        self.key(3)

    def back(self):
        self.key(4)

    def recents(self):
        self.key(187)

    def wake(self):
        self.key(224)  # KEYCODE_WAKEUP

    def lock(self):
        self.key(26)   # KEYCODE_POWER（亮屏时锁屏）

    def unlock(self):
        """上滑解锁（无密码时有效）。"""
        self.wake()
        time.sleep(0.3)
        w, h = self.window_size()
        self.swipe(w / 2, h * 0.8, w / 2, h * 0.25, 0.25)

    # ---------------- 文本输入 ----------------

    def has_adb_keyboard(self):
        if self._adb_kb is None:
            try:
                out = self.shell("pm list packages com.android.adbkeyboard")
                self._adb_kb = "com.android.adbkeyboard" in out
            except Exception:
                self._adb_kb = False
        return self._adb_kb

    def text(self, s):
        """发送文本。中文需要设备装有 ADBKeyboard（README 有说明）。"""
        self._begin_action()
        try:
            if self.has_adb_keyboard():
                self.shell(
                    f'am broadcast -a ADB_INPUT_TEXT --es msg "{s}"', timeout=10
                )
            else:
                # input text 只支持 ASCII；空格用 %s 转义
                safe = s.replace(" ", "%s").replace('"', r'\"').replace("$", r"\$")
                self.shell(f'input text "{safe}"', timeout=10)
        finally:
            self._end_action()

    # ---------------- App 管理 ----------------

    def launch(self, package):
        """通过 monkey 拉起主 Activity（无需知道 Activity 名）。"""
        out = self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=15)
        if "No activities found" in out or "Error" in out:
            raise RuntimeError(f"无法启动 {package}: 应用不存在或没有 Launcher 入口")

    def terminate(self, package):
        self.shell(f"am force-stop {package}", timeout=10)

    def foreground(self):
        """返回前台应用 (package, activity)。"""
        out = self.shell("dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity'")
        m = re.search(r"u0\s+(\S+)/(\S+)", out)
        if not m:
            out2 = self.shell("dumpsys window | grep mCurrentFocus")
            m = re.search(r"u0\s+(\S+)/(\S+)", out2)
        if not m:
            return None
        return {"package": m.group(1), "activity": m.group(2)}

    def apps(self):
        """已装第三方应用包名列表。"""
        out = self.shell("pm list packages -3")
        return sorted(
            line.replace("package:", "").strip()
            for line in out.splitlines()
            if line.strip()
        )

    def background_apps(self):
        """枚举运行中的第三方应用进程（无需 root）。

        设备实际格式：`  *APP* UID 10056 ProcessRecord{xxx 11759:com.sixzs.local/u0a56}`
        按行解析 *APP* 记录（排除 *PERS* 常驻进程），只保留含 "." 的包名。
        """
        out = self.shell("dumpsys activity processes", timeout=25)
        pkgs = set()
        for line in out.splitlines():
            if "*APP*" not in line:
                continue
            m = re.search(r"ProcessRecord\{[^}]*?\s\d+:([\w.]+)", line)
            if m and "." in m.group(1):
                pkgs.add(m.group(1))
        return sorted(pkgs)

    def kill_background(self, dry_run=False, keep_foreground=False):
        """清理后台：默认回主屏后 am kill-all（只杀缓存进程，不杀前台）。"""
        if dry_run:
            return {"dry_run": True, "candidates": self.background_apps()}
        fg = self.foreground()
        if not keep_foreground:
            self.home()
            time.sleep(0.5)
        self.shell("am kill-all", timeout=15)
        return {"dry_run": False, "foreground_before": fg}

    # ---------------- 元素树 ----------------

    def tree(self):
        """uiautomator dump 返回当前窗口的 XML 元素树。"""
        if self.demo:
            return "<hierarchy demo='true'></hierarchy>"
        self.shell("uiautomator dump /sdcard/as_dump.xml", timeout=20)
        out = self.exec_out("cat /sdcard/as_dump.xml", timeout=15)
        self.shell("rm /sdcard/as_dump.xml", timeout=8)
        try:
            # 某些设备输出带 "UI hierchary dumped to" 前缀，只保留 XML 部分
            xml = out.decode("utf-8", "replace")
            i = xml.find("<?xml")
            if i >= 0:
                xml = xml[i:]
            elif "<hierarchy" in xml:
                xml = xml[xml.find("<hierarchy"):]
            return xml
        except Exception:
            return ""

    # ---------------- 方向控制 ----------------

    def set_orientation(self, mode):
        """mode: 'auto' | 0 | 1 | 3  （user_rotation: 0=竖 1=横90 3=横270）。"""
        if mode == "auto":
            self.shell("settings put system accelerometer_rotation 1")
        else:
            self.shell("settings put system accelerometer_rotation 0")
            self.shell(f"settings put system user_rotation {int(mode)}")

    # ---------------- 安装 APK ----------------

    def install_apk(self, apk_path):
        out, err, rc = ctl.run(["install", "-r", "-d", apk_path], timeout=180,
                               serial=self._serial_or_raise())
        text = (out + err).strip()
        ok = "Success" in text
        return {"ok": ok, "output": text}

    def uninstall(self, package):
        out, err, rc = ctl.run(["uninstall", package], timeout=60,
                               serial=self._serial_or_raise())
        return {"ok": "Success" in (out + err), "output": (out + err).strip()}


DEV = Device()


# ---------------------------------------------------------------------------
# 脚本执行引擎
# ---------------------------------------------------------------------------

class ScriptRunner:
    """同一时刻只允许一个脚本运行（与 iPhone-Studio 一致，409 冲突）。

    注入 runtime: device/d, sleep, log, print, adb(ctl模块), time, json, os, Path
    停止用 PyThreadState_SetAsyncExc 强制中断。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.state = {"running": False, "started": 0, "name": None, "error": None}

    def run(self, code, name="<inline>"):
        with self.lock:
            if self.state["running"]:
                raise HTTPException(409, "已有脚本在运行，请先停止")
            self.state = {"running": True, "started": time.time(),
                          "name": name, "error": None}
        t = threading.Thread(target=self._worker, args=(code, name), daemon=True)
        self.thread = t
        t.start()
        return {"ok": True}

    def _worker(self, code, name):
        import ctypes
        tid = threading.get_ident()
        try:
            log(f"▶ 脚本开始: {name}")
            runtime = make_runtime()
            exec(compile(code, name, "exec"), {"__name__": "__main__"}, runtime)
            log(f"■ 脚本结束: {name}")
        except _StopScript:
            log(f"■ 脚本被手动停止: {name}")
        except Exception as e:
            self.state["error"] = f"{type(e).__name__}: {e}"
            log(f"✗ 脚本出错: {type(e).__name__}: {e}")
            log(traceback.format_exc(limit=4), key="tb")
        finally:
            self.state["running"] = False

    def stop(self):
        """强制中断运行中的脚本线程。"""
        if not self.state["running"] or self.thread is None:
            return {"ok": False, "msg": "没有脚本在运行"}
        # PyThreadState_SetAsyncExc 需要 ctypes 找到线程 id；这里用
        # 轮询标志 + 异步异常双保险
        import ctypes
        tid = self.thread.ident
        if tid:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(tid), ctypes.py_object(_StopScript)
            )
        return {"ok": True}


class _StopScript(Exception):
    pass


class ScriptDevice:
    """暴露给在线脚本的 device API —— 与 iPhone-Studio 保持同名同参。"""

    def __init__(self, dev):
        self._dev = dev
        self.session = None  # 兼容占位：Android 无需 session

    @property
    def online(self):
        return not self._dev.demo

    @property
    def serial(self):
        return self._dev._serial

    def tap(self, x, y):
        return self._dev.tap(x, y)

    def double_tap(self, x, y):
        return self._dev.double_tap(x, y)

    def swipe(self, x1, y1, x2, y2, duration=0.3):
        return self._dev.swipe(x1, y1, x2, y2, duration)

    def swipe_dir(self, direction, duration=0.35, margin=0.22):
        return self._dev.swipe_dir(direction, duration, margin)

    def swipe_up(self, duration=0.35):
        return self._dev.swipe_dir("up", duration)

    def swipe_down(self, duration=0.35):
        return self._dev.swipe_dir("down", duration)

    def swipe_left(self, duration=0.35):
        return self._dev.swipe_dir("left", duration)

    def swipe_right(self, duration=0.35):
        return self._dev.swipe_dir("right", duration)

    def text(self, s):
        return self._dev.text(s)

    def key(self, keycode):
        return self._dev.key(keycode)

    def home(self):
        return self._dev.home()

    def back(self):
        return self._dev.back()

    def recents(self):
        return self._dev.recents()

    def wake(self):
        return self._dev.wake()

    def lock(self):
        return self._dev.lock()

    def unlock(self):
        return self._dev.unlock()

    def launch(self, package):
        return self._dev.launch(package)

    def terminate(self, package):
        return self._dev.terminate(package)

    def app_current(self):
        return self._dev.foreground()

    def tree(self):
        return self._dev.tree()

    def apps(self):
        return self._dev.apps()

    def background_apps(self):
        return self._dev.background_apps()

    def kill_background(self, dry_run=False, keep_foreground=False):
        return self._dev.kill_background(dry_run, keep_foreground)

    def window_size(self):
        return self._dev.window_size()

    def rotation(self):
        return self._dev.rotation()

    def set_orientation(self, mode):
        return self._dev.set_orientation(mode)

    def screenshot(self, name=None):
        """保存截图到 shots/，返回路径字符串。"""
        jpeg, w, h, _ = self._dev.screenshot_jpeg(quality=90)
        name = name or f"shot_{int(time.time())}"
        p = SHOTS_DIR / f"{name}.jpg"
        p.write_bytes(jpeg)
        log(f"📷 截图已保存: {p.name} ({w}x{h})")
        return str(p)

    def install(self, apk_path):
        return self._dev.install_apk(apk_path)

    def uninstall(self, package):
        return self._dev.uninstall(package)

    def shell(self, cmd, timeout=20):
        """高级用法：直接执行 adb shell 命令并返回输出。"""
        return self._dev.shell(cmd, timeout=timeout)

    def sleep(self, t):
        """可中断的 sleep —— 循环里请用它而不是 time.sleep()。"""
        end = time.time() + t
        while time.time() < end:
            if RUNNER.stopping:
                raise _StopScript()
            time.sleep(min(0.1, end - time.time()))


RUNNER = ScriptRunner()
RUNNER.stopping = False


class ScriptRuntime:
    """包装 device，让 stop 时 RUNNER.stopping 置位 + 异步异常可命中 sleep。"""

    def __init__(self, dev):
        self._d = ScriptDevice(dev)

    def __getattr__(self, name):
        return getattr(self._d, name)


def make_runtime():
    import json as _json
    from pathlib import Path as _Path
    import time as _time
    import os as _os

    d = ScriptDevice(DEV)
    orig_sleep = d.sleep

    def sleep(t):
        if RUNNER.stopping:
            raise _StopScript()
        orig_sleep(t)

    d.sleep = sleep
    return {
        "device": d,
        "d": d,
        "sleep": sleep,
        "log": lambda *a: log(" ".join(str(x) for x in a)),
        "print": lambda *a, **k: log(" ".join(str(x) for x in a)),
        "adb": ctl,
        "time": _time,
        "json": _json,
        "os": _os,
        "Path": _Path,
    }


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(BASE / "web" / "index.html")


@app.get("/api/status")
def api_status():
    info = DEV.info()
    info["rotation"] = DEV.rotation() if not DEV.demo else 0
    info["screen_on"] = DEV.screen_on() if not DEV.demo else True
    info["script_running"] = RUNNER.state["running"]
    return info


@app.get("/api/screen")
def api_screen():
    try:
        jpeg, w, h, cached = DEV.screenshot_jpeg()
        return Response(jpeg, media_type="image/jpeg",
                        headers={"X-Screen-Size": f"{w}x{h}",
                                 "X-Frame-Cached": str(cached)})
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/stream")
def api_stream(interval: float = 0.5):
    """MJPEG 连续流。interval 是两次抓帧的最小间隔。"""
    interval = max(0.1, min(10, interval))

    def gen():
        boundary = b"--asframe\r\n"
        while True:
            try:
                jpeg, _, _, _ = DEV.screenshot_jpeg()
                yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            except Exception:
                yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + DEMO_ERR + b"\r\n"
            time.sleep(interval)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=asframe")


DEMO_ERR = b""


@app.post("/api/action")
async def api_action(req: Request):
    body = await req.json()
    typ = body.get("type")
    try:
        if DEV.demo:
            return {"ok": False, "demo": True, "msg": "演示模式：动作不下发"}
        if typ == "tap":
            DEV.tap(body["x"], body["y"])
        elif typ == "double_tap":
            DEV.double_tap(body["x"], body["y"])
        elif typ == "swipe":
            DEV.swipe(body["x1"], body["y1"], body["x2"], body["y2"],
                      body.get("duration", 0.3))
        elif typ == "swipe_dir":
            DEV.swipe_dir(body["dir"], body.get("duration", 0.35),
                          body.get("margin", 0.22))
        elif typ == "key":
            DEV.key(int(body["keycode"]))
        elif typ == "text":
            DEV.text(body.get("text", ""))
        elif typ == "home":
            DEV.home()
        elif typ == "back":
            DEV.back()
        elif typ == "recents":
            DEV.recents()
        elif typ == "wake":
            DEV.wake()
        elif typ == "lock":
            DEV.lock()
        elif typ == "unlock":
            DEV.unlock()
        elif typ == "rotate":
            DEV.set_orientation(body.get("mode", "auto"))
        else:
            raise HTTPException(400, f"未知动作 {typ}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/run")
async def api_run(req: Request):
    body = await req.json()
    code = body.get("code", "")
    if not code.strip():
        raise HTTPException(400, "代码为空")
    RUNNER.stopping = False
    try:
        return RUNNER.run(code, body.get("name", "<inline>"))
    except HTTPException:
        raise


@app.post("/api/stop")
def api_stop():
    RUNNER.stopping = True
    return RUNNER.stop()


@app.get("/api/run_state")
def api_run_state():
    return {"running": RUNNER.state["running"], "name": RUNNER.state["name"],
            "elapsed": round(time.time() - RUNNER.state["started"], 1) if RUNNER.state["running"] else 0}


@app.get("/api/logs/history")
def api_logs_history():
    return JSONResponse({"lines": LOGBUS.snapshot()})


@app.get("/api/logs")
def api_logs():
    """SSE 实时日志流。"""
    version_seen = [None]

    def gen():
        ver, lines = LOGBUS.since(0)
        yield f"data: {json.dumps({'reset': True, 'lines': lines}, ensure_ascii=False)}\n\n"
        last_ver = ver
        while True:
            ver, lines = LOGBUS.since(last_ver)
            if lines:
                yield f"data: {json.dumps({'lines': lines}, ensure_ascii=False)}\n\n"
            last_ver = ver
            time.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- 脚本库 ----------------

@app.get("/api/scripts")
def api_scripts():
    items = []
    for p in sorted(SCRIPTS_DIR.glob("*.py")):
        try:
            items.append({"name": p.name, "size": p.stat().st_size,
                          "mtime": int(p.stat().st_mtime)})
        except OSError:
            pass
    return {"scripts": items}


@app.get("/api/scripts/{name}")
def api_script_load(name: str):
    if "/" in name or "\\" in name or not name.endswith(".py"):
        raise HTTPException(400, "非法文件名")
    p = SCRIPTS_DIR / name
    if not p.exists():
        raise HTTPException(404, "脚本不存在")
    return {"name": name, "code": p.read_text("utf-8")}


@app.post("/api/scripts/{name}")
async def api_script_save(name: str, req: Request):
    if "/" in name or "\\" in name or not name.endswith(".py"):
        raise HTTPException(400, "非法文件名")
    body = await req.json()
    (SCRIPTS_DIR / name).write_text(body.get("code", ""), "utf-8")
    log(f"💾 脚本已保存: {name}")
    return {"ok": True}


@app.delete("/api/scripts/{name}")
def api_script_delete(name: str):
    if "/" in name or "\\" in name or not name.endswith(".py"):
        raise HTTPException(400, "非法文件名")
    p = SCRIPTS_DIR / name
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---------------- 元素树 / 应用 ----------------

@app.get("/api/tree")
def api_tree():
    try:
        return {"xml": DEV.tree()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/apps")
def api_apps():
    try:
        return {"apps": DEV.apps()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/foreground")
def api_foreground():
    try:
        return {"foreground": DEV.foreground()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/api/launch")
async def api_launch(req: Request):
    body = await req.json()
    pkg = (body.get("package") or "").strip()
    if not pkg:
        raise HTTPException(400, "package 为空")
    try:
        DEV.launch(pkg)
        log(f"🚀 已启动: {pkg}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/terminate")
async def api_terminate(req: Request):
    body = await req.json()
    pkg = (body.get("package") or "").strip()
    try:
        DEV.terminate(pkg)
        log(f"🛑 已结束: {pkg}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/background")
def api_background():
    try:
        return {"apps": DEV.background_apps()}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/api/kill_background")
async def api_kill_background(req: Request):
    body = await req.json()
    try:
        return DEV.kill_background(dry_run=body.get("dry_run", False))
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------- 安装 / 卸载 APK ----------------

@app.post("/api/install")
async def api_install(file: UploadFile = File(...)):
    tmp = BASE / f"_upload_{int(time.time())}.apk"
    try:
        tmp.write_bytes(await file.read())
        log(f"📦 开始安装 APK: {file.filename} ({tmp.stat().st_size/1e6:.1f} MB)…")
        r = DEV.install_apk(str(tmp))
        log(("✅ 安装成功: " if r["ok"] else "✗ 安装失败: ") + r["output"][:200])
        return r
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/uninstall")
async def api_uninstall(req: Request):
    body = await req.json()
    pkg = (body.get("package") or "").strip()
    try:
        return DEV.uninstall(pkg)
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------- 静态资源 ----------------

app.mount("/web", StaticFiles(directory=BASE / "web"), name="web")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8778)
    args = ap.parse_args()

    if DEV.refresh():
        log("adb 通道就绪，检测到在线设备")
    else:
        log("未发现设备，进入演示模式（连接后自动恢复）")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
