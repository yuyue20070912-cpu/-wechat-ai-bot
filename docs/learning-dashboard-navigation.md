# AI 伴学菜单与总览（Issue #13）

`/learning` 默认进入学习总览，自动使用已有档案对应的第一场考试（无档案时为 IELTS）；可在「考试与专项练习」切换考试、保存目标。抽屉提供七个学习视图和返回聊天入口。

视图切换仅改变外层 `hidden`，不重新初始化原有学习模块，不重新请求数据，不清空答案或暂停模考。原有考试切换、刷新、换题等操作仍按原有规则运行。任务的「去完成」也会切换至对应视图。

总览四项指标直接来自现有 overview API；诊断只展示客观筛查结果，AI 参考分单列。28 天活动日历直接使用服务器按用户时区生成的 `active_dates`。主题与做题明细复用现有 practice 和 overview 响应，按考试、日期筛选，以文本和空状态呈现。

**数据范围：**现有 API 最多返回最近 20 条练习与最近 12 次诊断。页面明确标注此范围，不能把返回记录当成 28 天全部做题总量；任务、复习、模考计入活动日，但不推算题量。本次没有改动 API、数据库或业务逻辑。

主按钮使用 `#173D80` 与白字（对比度约 10.4:1），hover `#214f9b`、active `#102c60`；禁用为灰色；丢弃未同步草稿用红色边框区分。考试选择卡和彩色指标卡保留。支持 safe-area、16px 表单输入、44px 触控目标、暗色模式、原生模态抽屉、遮罩关闭、Escape、Tab/Shift+Tab 焦点循环及关闭后恢复焦点。

## 验证

验证结果：完整 pytest 测试集 218 项通过；最后一次 UI 修改后，6 组浏览器用例复测全部通过；聊天流解析 4 项通过，所有学习脚本语法检查通过。

浏览器回归使用本地 Flask、临时 PostgreSQL 与测试 AI 替身；未调用真实付费 AI。

- Chromium / WebKit：390×844 明亮、375×812 暗色、1440×1000 桌面。
- 首屏仅总览；七个菜单视图及 IELTS/CET-4/CET-6 可达；各视图无横向滚动。
- 菜单键盘操作、焦点恢复、练习草稿与诊断选项跨菜单保留、模考答案与倒计时恢复。
- 真实 API 指标一致；无数据空状态；有记录后显示诊断主题；主按钮颜色断言。
- 任务、复习、练习反馈、错题收藏、模考听力/交卷/离线草稿回归。
- 诊断 AI 开关关闭时禁用；启用后仅主动点击调用一次；菜单与刷新不会调用 AI。
- 原有后端回归和聊天流解析测试；JS 语法检查。

截图由 WebKit 使用 iPhone 用户代理、触屏、设备缩放及上述窄屏尺寸生成，数据来自本地测试流程。**不是实体 iPhone Safari 或已安装 PWA 的真机测试。** 合并与生产部署仍须用户另行确认。

| 场景 | 明亮 | 暗色 |
| --- | --- | --- |
| 空状态总览 | [截图](learning-navigation-screenshots/iphone-light-overview.png) | [截图](learning-navigation-screenshots/iphone-dark-overview.png) |
| 抽屉菜单 | [截图](learning-navigation-screenshots/iphone-light-menu.png) | [截图](learning-navigation-screenshots/iphone-dark-menu.png) |
| 已有学习记录 | [截图](learning-navigation-screenshots/iphone-light-recorded-overview.png) | [截图](learning-navigation-screenshots/iphone-dark-recorded-overview.png) |

重新生成截图：设置 `LEARNING_SCREENSHOT_DIR=docs/learning-navigation-screenshots` 后运行 `python -m pytest -q tests/test_learning_ui.py`（需安装 Chromium 和 WebKit 及系统依赖）。
