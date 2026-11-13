# -*- coding: utf-8 -*-
# 每隔 2 秒截一张图，共 5 张（用 device.sleep 保证可中断）
for i in range(5):
    path = device.screenshot(f"连拍_{i+1:02d}")
    print(f"[{i+1}/5] {path}")
    device.sleep(2)
