# Android Studio

在浏览器里写代码，操作 USB/网络连接的真实 Android 设备：实时屏幕镜像、点击与拖拽、
按键与快捷网格、在线运行 Python 脚本、脚本库、元素树、实时日志、APK 上传安装，
以及横屏控制和后台应用清理。**全部在本机运行**：不需要账号、不需要云服务、
不上传手机画面，未连接设备时自动进入演示模式。

这是 [iPhone-Studio](https://github.com/123Jinfeiyu/iPhone-Studio) 的安卓版：
架构、界面与脚本 API 基本一一对应，只是底层通道从 **usbmux + WDA** 换成了 **adb**。

| iPhone-Studio | Android Studio |
|---|---|
| pymobiledevice3 usbmux 端口转发 | adb platform-tools（无需转发） |
| WebDriverAgent (8100) | 原生 `input` / `uiautomator dump` |
| WDA `/screenshot` PNG 轮询 | `adb exec-out screencap -p` JPEG 轮询 |
| 坐标单位 pt | 坐标单位 px（`wm size` 实时读取） |
| 越狱 SSH 清理后台 | 免 root `am kill-all`（只杀缓存进程） |
| WDA 强制横屏常被拒 | `settings put system user_rotation` 多数设备可用 |

## 特性

- **实时画面**：`/api/screen` 单帧 JPEG、`/api/stream` MJPEG 连续流、8 档刷新频率、帧缓存、失败沿用上一帧、动作优先调度
- **触控按键**：点击 = `input tap`、拖拽 = `input swipe`、双击、四方向整屏滑动、HOME/返回/多任务/音量/通知
- **在线编程**：浏览器里写 Python、`Ctrl/⌘+Enter` 运行、可强制中断的「停止」、实时日志 SSE
- **脚本库**：保存到 `scripts/`、载入/删除、10 个内置示例
- **元素树**：`uiautomator dump`，前端美化显示 text/resource-id/bounds
- **应用管理**：按包名启动（monkey）/结束（force-stop）、前台应用、第三方应用列表
- **后台清理**：免 root `am kill-all`，支持 `dry_run` 只读预览
- **APK 安装**：网页上传 APK → `adb install -r -d` 直接装到设备
- **横屏**：`user_rotation` 强制旋转（免 root）+ 画面旋转预览（坐标逆映射）
- **文本输入**：英文走 `input text`；中文自动走 ADBKeyboard 广播（如已安装）
- **演示模式**：没插手机时用本地占位图演示界面，动作不下发

## 依赖

| 依赖 | 用途 |
|---|---|
| Python 3.10+ | 运行后端 |
| adb (Android platform-tools) | 唯一设备通道。自动按 `ANDROID_ADB` → PATH → `%LOCALAPPDATA%\Android\Sdk\platform-tools` 顺序查找 |
| USB 或网络连接的 Android 设备 | 开启「开发者选项 → USB 调试」 |
| fastapi / uvicorn / Pillow | HTTP 服务、截图编码 |

不需要 root、不需要在手机上装任何东西（中文输入可选装 ADBKeyboard）。

## 安装与启动

```bash
git clone https://github.com/123Jinfeiyu/Android-Studio.git
cd Android-Studio
pip install -r requirements.txt

# 设备开启 USB 调试后插上电脑（或 adb connect 手机IP:5555）
python server.py --port 8777
# Windows 也可以直接双击 start.bat
```

打开 **http://127.0.0.1:8777**。服务每 6 秒探测一次设备；顶栏连接胶囊显示在线状态
与机型/分辨率/旋转/电量。不通时点「重新连接」。

局域网访问：`python server.py --host 0.0.0.0 --port 8777` —— **仅限可信网络**，
因为在线脚本可执行任意 Python 代码，服务默认只监听本机。

## 第一个脚本

编辑器默认载入 `01_设备体检.py`，按 `Ctrl/⌘ + Enter` 运行：

```python
print("设备在线:", device.online)
print("前台应用:", device.app_current())
print("屏幕尺寸(px):", device.window_size())
print("截图已保存:", device.screenshot("体检截图"))
```

## 在线脚本 API

运行时注入 `device`（简写 `d`）、`sleep`、`log`、`adb`（ctl 模块）、`time`、`json`、
`os`、`Path`，`print()` 会写进日志面板。循环里请用 `device.sleep()` 而不是
`time.sleep()`，这样「停止」才能及时中断。

| 方法 | 说明 |
|---|---|
| `tap(x, y)` / `double_tap(x, y)` | 按 px 坐标轻触 / 双击 |
| `swipe(x1, y1, x2, y2, duration=0.3)` | 自定义起止点滑动 |
| `swipe_dir("up"/"down"/"left"/"right", duration=0.35, margin=0.22)` | 按实时屏幕尺寸整屏滑动；简写 `swipe_up()` 等 |
| `text("中文")` | 发送文本（中文需 ADBKeyboard） |
| `key(keycode)` / `home()` / `back()` / `recents()` | 按键 |
| `wake()` / `lock()` / `unlock()` | 亮屏 / 锁屏 / 上滑解锁 |
| `launch(bundle)` / `terminate(bundle)` / `app_current()` | App 管理 |
| `tree()` / `apps()` / `background_apps()` | 元素树 XML / 应用列表 / 后台进程 |
| `kill_background(dry_run=False, keep_foreground=False)` | 批量清理（免 root） |
| `screenshot(name)` | 保存到 `shots/`，返回路径 |
| `rotation()` / `set_orientation("auto"\|0\|1\|3)` | 方向查询与强制切换 |
| `install(apk_path)` / `uninstall(package)` | APK 安装 / 卸载 |
| `shell(cmd)` | 高级用法：直接执行 adb shell |
| `window_size()` | 实时逻辑尺寸 (w, h)，单位 px |

## 界面能做什么

| 区域 | 操作 |
|---|---|
| 画面 | 点一下轻触、按住拖拽滑动、双击；左上 `⛶ 全屏` / `铺满` |
| 画面上方 | 刷新档位（关闭 / MJPEG / 0.2s ~ 5s）、`刷新`、`存图` |
| 左栏 | 竖屏 / 横屏 / `旋转画面`、`← ↑ ↓ →`、快捷按键网格、文本输入、包名启动/结束、APK 上传安装 |
| 右栏 | `编辑器` / `脚本库` / `元素树` / `API 速查` 四个标签页 + 实时日志 |
| 快捷键 | `Ctrl/⌘+Enter` 运行、`Ctrl/⌘+S` 保存、`Tab` 缩进、`Esc` 关帮助 |

## 架构

```
浏览器 ── /api/screen | /api/stream | /api/action | /api/run | /api/logs (SSE)
   │
FastAPI (server.py)
   ├─ Device    · io_lock 串行 · adb shell 唯一出口 · 帧缓存 · 动作优先
   ├─ 脚本执行  · 线程 exec + ScriptDevice API + PyThreadState_SetAsyncExc 强制中断
   └─ 日志      · deque + SSE + 按 key 限流
   │
   adb (platform-tools)
   └── USB / TCP ── Android 设备
```

关键点（与 iPhone-Studio 同一套设计）：
- 所有 adb 命令都经同一个 `io_lock`，避免 screencap 与 input 并发互踩；
- 截图只抓一份并缓存为 JPEG（同 0.35s 内复用同一帧）；
- 有动作排队时抓帧主动让路；
- 屏幕尺寸每次实时读取，绝不缓存（分辨率修改 / 旋转会变）。

## 已知限制

| 限制 | 说明 |
|---|---|
| 帧率 | `screencap` 每帧约 0.3–1s，真实画面约 **1–2 fps**。要更流畅可后续接 scrcpy/minicap |
| 中文输入 | `input text` 不支持 Unicode；装 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard) 后自动走广播通道 |
| 解锁 | `unlock()` 是上滑手势，仅对无密码锁屏有效 |
| 元素树 | `uiautomator dump` 约 1–3 秒，游戏/自绘界面可能抓不到节点 |
| 多脚本 | 同一时刻只允许一个脚本运行，重复运行返回 409 |
| 强制横屏 | 个别 ROM 禁用 `user_rotation`，此时画面不会转，可用「旋转画面」预览代替 |

## 目录结构

```
Android-Studio/
├── ctl.py             adb 定位 / 命令封装 / 调试入口（被 server.py 复用）
├── server.py          FastAPI 后端：设备、帧缓存、动作、脚本、日志、API
├── web/
│   ├── index.html     页面骨架与控件
│   ├── app.css        布局 / 横屏 / 全屏 / 主题
│   └── app.js         交互 + API 速查 + 坐标逆映射
├── scripts/           示例脚本与用户保存的脚本
├── shots/             运行期截图输出
├── demo/              演示模式占位图
├── start.bat          Windows 一键启动
└── requirements.txt
```

## 安全与隐私

- 服务默认只监听 `127.0.0.1`；在线脚本可执行任意 Python，`--host 0.0.0.0` 仅限可信局域网。
- 手机截图、`shots/`、上传的临时 APK 均不进入仓库。

## 致谢

设计模式致敬 [iPhone-Studio](https://github.com/123Jinfeiyu/iPhone-Studio)。
底层依赖 adb platform-tools、FastAPI、Pillow。

MIT License
