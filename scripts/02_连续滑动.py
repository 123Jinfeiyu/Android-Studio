# -*- coding: utf-8 -*-
# 连续上滑：刷信息流（抖音/小红书/微博通用）
for i in range(10):
    print(f"第 {i+1}/10 次上滑")
    device.swipe_up(duration=0.4)
    device.sleep(1.2)
