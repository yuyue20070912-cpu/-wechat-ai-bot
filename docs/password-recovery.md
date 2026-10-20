# 手机号找回密码

本变更仅新增登录页入口、独立 `/forgot-password` 页面及找回密码 API，并增加密码重置所必需的会话撤销检查。模型、Render、AI 伴学、会员/支付及聊天页面布局均未修改。

## 阿里云配置（待管理员在批准发布时设置）

使用**号码认证服务 PNVS**，不是普通短信服务。服务器环境变量：

- `ALIBABA_CLOUD_ACCESS_KEY_ID`
- `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
- `ALIYUN_PNVS_SIGN_NAME`：号码认证控制台可用的签名
- `ALIYUN_PNVS_TEMPLATE_CODE`：与签名配套、含 `code` 和 `min` 参数的模板
- `ALIYUN_PNVS_SCHEME_NAME`：为密码找回配置独立方案，与其他验证码用途隔离；发送与核验必须一致

使用最小权限 RAM 身份，授权 `dypns:SendSmsVerifyCode` 和 `dypns:CheckSmsVerifyCode`，不要把密钥放进代码、浏览器或日志。保留原 `SESSION_SECRET_KEY` 和 `DATABASE_URL`。缺少任一配置返回 503 提示，不发送模拟验证码，也无法重置密码。权限不足、服务未开通、余额/模板问题或网络故障同样阻止继续；不把供应商原始错误返回或记录到日志。

官方接口：
- [SendSmsVerifyCode](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-sendsmsverifycode)
- [CheckSmsVerifyCode](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-checksmsverifycode)

## 安全设计

1. 服务端使用阿里云 SDK，经 HTTPS 请求 `dypnsapi.aliyuncs.com`。阿里云生成6位数字码，300秒有效，60秒间隔，重发覆盖旧码，不返回验证码。核验必须同时满足 `Success=true`、`Code=OK`、`Model.VerifyResult=PASS`。
2. PostgreSQL 保存找回挑战、尝试次数与凭证；多线程/多实例共用限制。手机号每60秒最多一次、15分钟最多5次、24小时最多10次；IP每15分钟最多20次。核验与重置分别按手机号5次/IP20次每15分钟限制，每个挑战最多核验5次（包括供应商超时）。限制基于 `request.remote_addr`，沿用应用现有部署信任边界，不信任客户端伪造的转发头；代理可能使多用户共享IP额度。
3. 未注册手机号返回相同文案与挑战结构，不发送短信，也不能取得重置凭证。不会新建账号。此措施避免直接通过API响应枚举账号，但不保证网络耗时完全一致。
4. 验证成功后签发256位随机、5分钟有效的一次性重置凭证，仅在浏览器内存传递；数据库只保存其 SHA-256 摘要。重发立即废弃旧挑战和旧凭证。行锁与原子事务防止并发重放；密码变化也会废弃此前签发的凭证。
5. 新密码10–128位，包含英文字母及数字或符号，不能与旧密码相同。使用 Werkzeug scrypt 加盐哈希，更新原用户行，不改用户ID、会员、订单、用量或学习数据。
6. 密码更新、会话版本递增、凭证消费在同一事务提交。每次携带登录会话的后续请求都查询服务端版本，旧 Cookie 无法重放；数据库故障时拒绝验证。兼容尚未带版本的旧会话（按0处理），无需批量登出未重置用户。已开始执行的请求/流不会被中途撤回。现有主动修改密码也递增版本，以避免较早的找回凭证覆盖新密码。
7. Cookie保持既有 HttpOnly/Secure/SameSite=Lax；找回API仅接受JSON，不开放跨域。页面和API禁缓存，页面加同源CSP、禁止嵌入和Referrer。日志不包含密钥、验证码或重置凭证。

## 数据库与发布

沿用现有首次初始化机制，在 `users` 追加 `session_version BIGINT NOT NULL DEFAULT 0`，新增 `password_recoveries` 表。DDL可重复执行，不删除/迁移原用户和业务表。过期一天以上的找回记录在下次发送时清理。

此PR仅供审核，不执行合并或部署。批准上线前需在目标环境完成上述PNVS配置，并用注册测试号码执行一次真实短信发送/核验/重置；自动化测试使用供应商替身，不产生真实短信费用。没有生产凭据，因此本次不声称完成真实阿里云联调。
