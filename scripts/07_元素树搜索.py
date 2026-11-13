# -*- coding: utf-8 -*-
# 抓元素树并搜索含指定文本的节点（uiautomator dump 约 1-3 秒）
import re

xml = device.tree()
print("XML 长度:", len(xml))

# 找出所有带 text 的节点
for m in re.finditer(r'text="([^"]{1,40})"[^>]*bounds="(\[[^\]]+\]\[[^\]]+\])"', xml):
    print(f"  {m.group(1)!r:30s} @ {m.group(2)}")
