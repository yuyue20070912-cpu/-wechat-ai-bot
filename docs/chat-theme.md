# fishyeah Chat 外观设置

基于最新 main `0910ae7`，独立分支 `feature/chat-system-theme`。仅修改主聊天页主题、相关配色及测试，不修改 AI 伴学、音频、AI 模型、登录规则、会员/支付规则或额度。

## 行为

- 无偏好时默认「跟随系统」，通过 `prefers-color-scheme` 实时响应系统外观，无需刷新，无定位或日出日落计算。
- 现有侧边菜单新增「外观设置」，提供跟随系统、浅色模式、深色模式。使用原生 details、fieldset、radio，支持键盘、焦点提示和至少 44px 的选项触控区域。
- 完全删除顶栏旧主题按钮、对应伪元素与 `body.classList.toggle("dark")` 处理器。顶栏保留新对话；菜单、账号安全、会员入口均保留。
- 手动选择使用 `fishyeah-chat-theme` 存入 localStorage；浅色/深色选择优先于系统，刷新、关闭后重新打开页面均恢复。其他标签页的明确修改同步到当前页。
- 头部同步脚本在绘制前应用根元素主题；同时设置 `color-scheme` 和 `theme-color`。页面恢复/重新可见时重新应用系统外观；旧版 Safari 使用 `MediaQueryList.addListener` 回退。
- 存储被浏览器禁用时不报错，当前页面仍可选择外观；此时无法保证刷新后的持久化。

## 旧偏好兼容

检查最新 main 及可见历史后，旧实现仅切换页面内 body class，未发现持久化主题键；无法恢复从未保存的历史选择。为旧客户端或定制版本提供以下保守兼容，不删除原始键：

| 优先级 | 键 | 接受值 |
| --- | --- | --- |
| 1 | fishyeah-chat-theme | system / light / dark |
| 2 | fishyeah-theme | system / auto / light / dark |
| 3 | theme | system / auto / light / dark |
| 4 | darkMode | true / false（字符串） |

有效新偏好始终优先。旧值 auto 映射为 system，darkMode 的 true/false 映射为 dark/light；有效旧值迁移到新键。其他值不解释为手动选择。

## 配色与范围

沿用现有 CSS 变量与布局，主聊天页以根元素 `data-theme` 为唯一主题来源。统一背景、气泡、输入框、按钮、菜单、外观图标、登录、会员表单及订单状态颜色；深色背景使用与伴学一致的中性色。保留绿色用户气泡、原有头像、聊天排列、输入与发送按钮位置；主操作使用深蓝色。二维码不施加颜色反转。

修改文件：

- `bot.py`：主页面元数据、主题脚本/样式引用、菜单外观选项、移除旧按钮及处理器。
- `static/chat-theme.js`：启动、跟随系统、手动持久化、兼容迁移与跨标签同步。
- `static/chat-theme.css`：统一配色和菜单外观选项。
- `static/fishyeah-ui.css`、`static/chat-focus.css`：复用原有暗色规则，改用统一根元素主题；移除旧按钮样式。
- `tests/test_chat_theme.py`：6 项浏览器测试。
- `.github/workflows/tests.yml`：增加主题脚本语法检查。
- 本说明及 `docs/chat-theme-screenshots/`：评审截图。

## 验证

专项浏览器测试 6 项通过。覆盖实时系统变化、页面不刷新/草稿保留、手动优先、刷新和重新打开、跨标签页、旧值迁移与新键优先、旧按钮删除、存储禁用、Safari 旧监听接口、正常聊天流、真实注销/密码登录、会员入口和订单查询、新对话。正常文字与背景的 9 组实算对比度均至少 4.5:1。

覆盖 WebKit iPhone 390×844、WebKit 桌面 1440×1000、Chromium 桌面 1440×1000，以及 standalone 标识场景。截图与测试是浏览器仿真，未做实体 iPhone、实体 Mac 或实际安装的主屏幕 PWA 真机验证。保留原有 Apple Web App 元数据与 safe-area 布局。

使用临时 PostgreSQL 和模拟聊天流，未调用真实 AI 或收费 API。完整本地回归 **240 passed**，聊天流解析 **4 passed**，6 份 JavaScript 语法检查和 `git diff --check` 通过。远端 CI 结果记录在 Draft PR。

| 场景 | 浅色 | 深色 |
| --- | --- | --- |
| 手机聊天 | [截图](chat-theme-screenshots/390-light-chat.png) | [截图](chat-theme-screenshots/390-dark-chat.png) |
| 手机菜单/外观设置 | [截图](chat-theme-screenshots/390-light.png) | [截图](chat-theme-screenshots/390-dark.png) |
| 桌面菜单/外观设置 | [截图](chat-theme-screenshots/1440-light.png) | [截图](chat-theme-screenshots/1440-dark.png) |

重新生成截图：设置 `CHAT_THEME_SCREENSHOT_DIR=docs/chat-theme-screenshots`，运行 `python -m pytest -q tests/test_chat_theme.py`。

仅创建 Draft PR，未合并 main，未部署 Render。
