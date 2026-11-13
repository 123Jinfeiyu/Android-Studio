# -*- coding: utf-8 -*-
# 清理后台：先只读预览（dry_run），确认后再真清理
print("后台缓存进程（预览）:")
for p in device.background_apps():
    print("  -", p)

# dry_run=True 只预览不执行；确认无误后改为：
result = device.kill_background(dry_run=False)
print("已回主屏并执行 am kill-all:", result)
