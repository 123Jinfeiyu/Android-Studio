# -*- coding: utf-8 -*-
# 启动指定应用 → 截图 → 结束（把包名换成你要测的）
PKG = "com.xingin.xhs"   # 小红书

print("当前前台:", device.app_current())
device.launch(PKG)
device.sleep(3)
print("启动后前台:", device.app_current())
print("截图:", device.screenshot(f"{PKG}_首页"))
device.swipe_up()
device.sleep(1)
print("截图:", device.screenshot(f"{PKG}_滑动后"))
device.terminate(PKG)
print("已结束应用")
