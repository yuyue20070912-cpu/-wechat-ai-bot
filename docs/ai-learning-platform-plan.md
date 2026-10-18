# fishyeah Chat — AI 伴学平台开发计划

## 范围
支持 IELTS、CET-4、CET-6；保持现有聊天、登录、消息额度、会员、订单流程不变。开发仅在 feature/ai-learning-platform 分支进行，未经明确批准不得合并 main 或部署生产。

## 已检查的技术基础
- Flask + Gunicorn + OpenAI Python SDK
- PostgreSQL / psycopg + 连接池
- Session 登录与手机号账户
- 每日免费额度及会员消息计费
- 现有核心业务集中于 bot.py，新增功能应模块化

## 分阶段路线
1. **基础设施**：学习中心 UI、考试类型/目标、学习档案、模块化 API、数据库迁移及单元测试。
2. **核心练习**：口语（先文字后语音）、写作批改、词汇、听力、阅读、翻译；结构化反馈及失败处理。
3. **个性化**：诊断测试、每日计划、错题本、间隔复习、进度仪表板。
4. **考试模拟**：IELTS 分项模拟、CET-4/6 模拟、计时、评分参考与解析；禁止声称 AI 估分等同官方成绩。
5. **上线准备**：限额与成本、数据隔离、内容合规、无障碍与移动端体验、灰度发布和监控。

## 建议模块边界
- learning/routes.py：学习 API（要求登录、输入校验、限流）
- learning/prompts.py：分考试/题型的教学提示词
- learning/services.py：调用 AI、结构化输出和错误处理
- learning/repository.py：用户学习数据访问
- static/：独立学习中心 UI（具体路径需先确认项目实际结构）
- tests/：学习功能、回归、权限、配额测试

## 数据设计草案
- learning_profiles(user_id, exam_type, target_score, current_level, created_at, updated_at)
- learning_sessions(id, user_id, exam_type, skill, started_at, finished_at)
- learning_attempts(id, session_id, user_id, question_type, answer, feedback, created_at)
- learning_review_items(id, user_id, item_key, due_at, interval_days, ease_factor)
- learning_plans(id, user_id, plan_date, tasks_json, completed_json)

迁移须采用可重复执行且兼容旧版本的方式；不要在请求路径执行破坏性 DDL。

## 验收与风险控制
- 登录用户只能读取/修改自己的学习数据；未登录请求返回 401。
- AI 请求必须受服务器端配额控制；异常时正确结算/退还额度。
- 不暴露 API Key、手机号、答案内容或管理凭证。
- 保留原聊天流式输出、登录、会员和订单回归测试。
- 音频录制必须明确征求用户授权，设置大小/时长上限和保留策略。
- 题库必须确认版权或使用原创/授权材料。
- 测试通过后先提交 PR；不自动合并或部署。

## 第一批待办
- [x] 盘点现有前端文件、聊天 API、静态路由和测试文件
- [x] 确认现有文本模型配置与费用控制方案（真实价格、模型权限及音频能力留待独立验证）
- [x] 实现学习档案和考试类型 API
- [x] 实现学习中心移动端入口
- [x] 编写测试并创建 PR 草稿

## 实施记录

- [第一阶段](ai-learning-platform-phase-1.md)：学习中心、三类考试和独立档案。
- [第二阶段](ai-learning-platform-phase-2.md)：六类文字训练、结构化 AI 反馈、共享消息额度和独立调用预算。语音独立开发。
- [第三阶段](ai-learning-platform-phase-3.md)：三考试入门诊断、可选有预算的 AI 解读、每日任务、错题本、间隔复习与数据中心。
- [迁移与回退方案](learning-rollout-safety.md)：显式迁移、事务与锁超时、隔离库演练、数据保留与故障恢复。
