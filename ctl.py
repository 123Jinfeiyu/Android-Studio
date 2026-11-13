# -*- coding: utf-8 -*-
"""
ctl.py — adb 命令行封装 + 调试入口（被 server.py 复用）

Android Studio 不需要端口转发：adb 本身就是通道。
所有对设备的操作最终都会落到这里的 shell() / run()。

用法：
    python ctl.py devices          # 列出设备
    python ctl.py info             # 第一台设备的信息
    python ctl.py shell <cmd>      # 对第一台设备执行 shell 命令
    python ctl.py size             # 屏幕尺寸
"""
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# adb 定位
# ---------------------------------------------------------------------------

_ADB_CANDIDATES = [
    os.environ.get("ANDROID_ADB"),
    shutil.which("adb"),
    os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
    os.path.expandvars(r"%ANDROID_HOME%\platform-tools\adb.exe"),
    r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
    "/usr/bin/adb",
    "/usr/local/bin/adb",
]


def find_adb() -> str:
    """按优先级找到可用的 adb 可执行文件。"""
    for c in _ADB_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError(
        "找不到 adb。请安装 Android platform-tools，"
        "或设置环境变量 ANDROID_ADB 指向 adb.exe"
    )


ADB = find_adb()


# ---------------------------------------------------------------------------
# 基础执行
# ---------------------------------------------------------------------------

def run(args, timeout=20, binary=False, serial=None):
    """执行一条 adb 命令，返回 (stdout, stderr, returncode)。"""
    cmd = [ADB]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return (b"", b"timeout", 124) if binary else ("", "timeout", 124)
    if binary:
        return p.stdout, p.stderr, p.returncode
    return (
        p.stdout.decode("utf-8", "replace"),
        p.stderr.decode("utf-8", "replace"),
        p.returncode,
    )


def shell(cmd, timeout=20, serial=None):
    """adb shell <cmd>，返回 stdout 文本。失败抛 RuntimeError。"""
    out, err, rc = run(["shell", cmd], timeout=timeout, serial=serial)
    if rc != 0 and not out:
        raise RuntimeError(f"adb shell 失败({rc}): {err or cmd}")
    return out


def exec_out(cmd, timeout=25, serial=None):
    """adb exec-out <cmd>，返回二进制 stdout（适合截图）。"""
    out, err, rc = run(["exec-out", cmd], timeout=timeout, binary=True, serial=serial)
    if rc != 0 or not out:
        raise RuntimeError(f"adb exec-out 失败({rc}): {err or cmd}")
    return out


# ---------------------------------------------------------------------------
# 设备枚举
# ---------------------------------------------------------------------------

def devices():
    """返回 [{'serial': ..., 'state': ...}, ...]，忽略 offline。"""
    out, _, _ = run(["devices"], timeout=10)
    result = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        state = state.split()[0]
        result.append({"serial": serial, "state": state})
    return result


def online_devices():
    return [d for d in devices() if d["state"] == "device"]


def first_serial():
    devs = online_devices()
    return devs[0]["serial"] if devs else None


# ---------------------------------------------------------------------------
# 常用信息解析
# ---------------------------------------------------------------------------

def parse_wm_size(text):
    """从 `wm size` 输出解析出 (w, h)。优先 Override，其次 Physical。"""
    m = re.search(r"Override size:\s*(\d+)x(\d+)", text)
    if not m:
        m = re.search(r"Physical size:\s*(\d+)x(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def parse_wm_density(text):
    m = re.search(r"Override density:\s*(\d+)", text)
    if not m:
        m = re.search(r"Physical density:\s*(\d+)", text)
    return int(m.group(1)) if m else 0


def parse_rotation(dumpsys_display_text):
    """从 dumpsys display 解析旋转角。"""
    m = re.search(r"mDisplayRotation=\s*(\d+)", dumpsys_display_text)
    if m:
        return int(m.group(1))
    m = re.search(r"mRotation=\s*(\d+)", dumpsys_display_text)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# 命令行调试入口
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    op = sys.argv[1]
    if op == "devices":
        for d in devices():
            print(f"{d['serial']}  {d['state']}")
    elif op == "info":
        s = first_serial()
        if not s:
            print("没有在线设备")
            return
        size = parse_wm_size(shell("wm size", serial=s))
        dens = parse_wm_density(shell("wm density", serial=s))
        ver = shell("getprop ro.build.version.release", serial=s).strip()
        model = shell("getprop ro.product.model", serial=s).strip()
        print(f"serial={s} model={model} android={ver} size={size[0]}x{size[1]} density={dens}")
    elif op == "size":
        s = first_serial()
        print(parse_wm_size(shell("wm size", serial=s)))
    elif op == "shell":
        s = first_serial()
        print(shell(" ".join(sys.argv[2:]), serial=s, timeout=60))
    else:
        print(f"未知操作: {op}")


if __name__ == "__main__":
    main()
