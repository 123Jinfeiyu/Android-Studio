# -*- coding: utf-8 -*-
# 中文输入测试：需要设备安装 ADBKeyboard
# adb install ADBKeyboard.apk 后设置其为默认输入法
device.launch("com.android.settings")
device.sleep(2)

if not device.text("你好，安卓世界"):
    print("发送失败")
else:
    print("中文已发送（若无 ADBKeyboard，英文会成功，中文会静默失败）")
device.screenshot("中文输入")
