# -*- coding: utf-8 -*-
"""电脑操作 Agent。

分层（越往下越危险，也越少改）：

```
prompts.py   提示词与观察渲染      —— 纯字符串，改了不影响逻辑
protocol.py  <intent><code> 解析 + AST 校验
llm.py       补全 + token 记账
sandbox.py   受限执行环境（不叫沙箱，见模块注释）
tools.py     工具实现 + 风险标注 + 路径白名单
win32.py     ctypes 封装
gate.py      风险分级 + 确认策略
runner.py    主循环
```

**这个包同样不许 import Qt**：runner 跑在 daemon 线程里，所有跟界面有关的
东西通过 ``runner`` 抛出的回调转给主窗口。
"""
from __future__ import annotations

__all__ = ["protocol", "prompts", "llm"]
