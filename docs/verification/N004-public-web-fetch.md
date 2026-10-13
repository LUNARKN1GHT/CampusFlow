# N004 公开网页安全抓取

负责人：金嘉睿。叶子任务：#144；父任务：#18（E60）。

## 交付范围

新增可由网页导入用例调用的 `PublicWebFetcher.fetch(requested_url)`，只在明确调用时读取给定网址及有限次跳转。不扫描站点、不读取正文中的链接、不运行脚本、不创建资料或任务。用户选择使用 Python 标准库，未增加安装包或修改锁文件。

这是网页导入的底层抓取组件，尚不是完整网页导入功能。N005 负责保存网页快照与发布来源，N006 负责网页正文提取与段落定位；页面返回 200 的登录表单也需在正文解析时识别。本任务没有新增未受保护的 HTTP 接口。后续入口必须沿用登录与工作空间授权检查，再调用该组件，不接受资料正文中的指令作为抓取授权。

## 限制与失败行为

| 项目 | 行为 |
| --- | --- |
| 协议与端口 | HTTP 80、HTTPS 443；拒绝网址中的账号密码、控制字符、反斜线和本地区域标识 |
| 地址 | IPv4/IPv6 公网地址；拒绝本机、内网、链路本地、保留、组播、特殊转换地址及 Azure 平台服务地址 |
| DNS | 域名解析出的所有地址都检查；混有私有地址时整体拒绝。连接固定到已检查的数字地址，避免连接时再次解析到内网 |
| HTTPS | 使用系统可信证书和原始域名验证，不关闭证书校验、不自动降级 |
| 跳转 | 默认最多 3 次；每一跳重新校验协议、地址和 DNS；支持相对地址，拒绝循环和缺失目标 |
| 大小 | 默认正文最多 2 MiB；检查声明长度和实际读取量，无声明长度、分块传输也受限制；不自动解压 |
| 时间 | 默认整个调用最多等待 15 秒，包含 DNS、连接、证书握手、响应头、正文和跳转；超时关闭已创建连接，不返回部分正文 |
| 并发 | 每进程最多 4 个抓取线程，满额立即返回繁忙；阻塞中的系统 DNS 不能强制取消，但占用原有名额，返回后不会继续连接 |
| 会话 | 不携带 Cookie、登录凭据，不继承环境代理，不自动重试；401/403 提示上传文件或粘贴原文 |
| 内容 | 仅接收 HTML、XHTML 和纯文本；拒绝压缩响应及其他文件类型；HTTP 失败不保存部分结果 |

默认限制可通过 `FetchLimits` 在可信后端代码中调整，不将这些参数暴露给网页内容或用户请求。当前只尝试 DNS 列表中首个已验证地址；不可达时提示重试，不绕过地址检查或转用代理。

## 调用示例

调用方先校验当前用户及目标空间，再显式调用：

```python
from campusflow.application.ports.web_fetch import WebFetchError
from campusflow.infrastructure.web_fetch import PublicWebFetcher

fetcher = PublicWebFetcher()
try:
    page = fetcher.fetch(user_requested_url)
except WebFetchError as error:
    # error.code 供业务流程判断；str(error) 为可展示的中文原因。
    # 不把失败写成成功导入，不记录包含查询参数的完整网址。
    raise
else:
    # 将 page.body、page.final_url、page.content_type 交给后续正文解析用例。
    pass
```

应用层契约位于 `backend/src/campusflow/application/ports/web_fetch.py`；实现位于 `backend/src/campusflow/infrastructure/web_fetch.py`。

## 验证方法

在 `backend/` 中执行：

```powershell
uv run --locked pytest tests/test_web_fetch.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

专项测试使用可控 DNS 与 socket 输入，并由 Python 的真实 HTTP 响应解析器处理响应；不访问真实外部网站，不使用 API 密钥。成功样本读取 N001 的 `samples/synthetic/n001/public-notice.html`。测试覆盖私有地址及特殊写法、相对/跨域/同域跳转、DNS 地址变化、证书检查、声明/流式/分块大小限制、超时取消、并发保护和仅处理显式网址。

2026-10-13 本地验证：完整后端 164 项通过（其中本任务专项 73 项），Ruff 检查与格式检查通过；前端锁定安装、类型检查与构建通过。完整后端使用独立的 `campusflow_n004_oct13_test` 数据库，未清理其他分支的数据。前端代码与已复验的 PR #325 提交 `2ab405c` 相同；该提交的三项真实 API 浏览器流程已在本机 Chrome 通过。

完整网页导入页面、正文解析和线上网站兼容性不在本任务验收范围。
