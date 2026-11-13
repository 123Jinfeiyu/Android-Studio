# -*- coding: utf-8 -*-
# 四方向整屏滑动演示：坐标按实时屏幕尺寸计算，横竖屏都正确
for d in ("up", "down", "left", "right"):
    print(f"向 {d} 滑动")
    device.swipe_dir(d, duration=0.35, margin=0.22)
    device.sleep(1.0)
