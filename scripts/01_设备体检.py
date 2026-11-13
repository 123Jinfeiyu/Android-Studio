# -*- coding: utf-8 -*-
# 设备体检：连接状态、前台应用、屏幕尺寸、系统信息
print("设备在线:", device.online)
print("序列号:", device.serial)
print("前台应用:", device.app_current())
print("屏幕尺寸(px):", device.window_size())
print("旋转状态:", device.rotation())
print("已装第三方应用数:", len(device.apps()))
print("后台缓存进程数:", len(device.background_apps()))
print("截图已保存:", device.screenshot("体检截图"))
