# -*- coding: utf-8 -*-
# 亮屏 → 等待 → 上滑解锁（无锁屏密码时有效）
device.wake()
device.sleep(0.8)
device.unlock()
print("屏幕状态:", "亮" if device.online else "?")
print("完成")
