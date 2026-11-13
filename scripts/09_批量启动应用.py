# -*- coding: utf-8 -*-
# 依次启动一组应用、停留、结束（用于批量冒烟测试）
APPS = [
    "com.android.settings",
    "com.android.deskclock",
]

for pkg in APPS:
    print(f"启动 {pkg} …")
    try:
        device.launch(pkg)
        device.sleep(2)
        print("  前台:", device.app_current())
        device.screenshot(pkg)
        device.terminate(pkg)
        device.sleep(0.5)
    except Exception as e:
        print("  ✗ 失败:", e)
print("批量测试完成")
