# hermes-chrome-plugin

让 Hermes Agent 操控你真实的 Chrome 浏览器——带着你所有的登录态、Cookie、Session 和扩展，通过配套的 Chrome 扩展走本地回环通信。

## 能做什么

当你在 Hermes 中和 Agent 对话时，Agent 可以直接操作你的 Chrome，就像你在操作一样。典型场景：

- **需要登录态的操作** — 查 Gmail 邮件、读 GitHub Issue、访问公司内网，Agent 带着你的登录态直接操作
- **网页自动化** — 填表单、点按钮、翻页、上传文件，Agent 替你完成
- **页面数据提取** — 抓取需要登录才能看的内容、监控网络请求、读控制台日志
- **截图分析** — 对页面截图、检查渲染效果

> 💡 不需要登录态的简单抓取，用 Hermes 内置的 `web_search` / `web_fetch` 更简单。需要登录态 = 用 `chrome_*`。

## 安装

### 1. 安装插件

在 Hermes 终端中：

```bash
hermes plugins install waitkeeper/hermes-chrome-plugin
hermes plugins enable hermes-chrome-plugin
hermes gateway restart
```

### 2. 加载 Chrome 扩展

1. 打开 Chrome，地址栏输入 `chrome://extensions`
2. 打开右上角 **开发者模式** 开关
3. 点击 **加载已解压的扩展程序**
4. 选择插件目录下的 `chrome-extension/` 文件夹：
   ```
   ~/.hermes/plugins/hermes-chrome-plugin/chrome-extension/
   ```
5. 确认面板中出现 **"Hermes Chrome Connector"**

### 3. 验证安装

确保 Chrome 保持打开，然后在 Hermes 中输入：

```
/chrome doctor
```

看到 `✓ Chrome is connected` 就说明安装成功。

## 在 Hermes 中使用

### 授权（必须）

出于安全考虑，Chrome 操控默认是**锁定的**。授权后 Agent 才能使用 `chrome_*` 工具：

```
/chrome authorize
```

授权选项：

| 命令 | 说明 |
|------|------|
| `/chrome authorize` | 授权 30 分钟（默认） |
| `/chrome authorize 2h` | 授权 N 分钟 |
| `/chrome authorize indefinite` | 永久授权（仅可信设备） |
| `/chrome revoke` | 立即撤销授权 |
| `/chrome status` | 查看当前状态 |

授权完成后，直接在 Hermes 对话中**用自然语言**让 Agent 操作浏览器即可，Agent 会自动选择合适的 `chrome_*` 工具。

### 操作示例

```
你: 帮我打开 GitHub，看看我的 notifications 页面有哪些未读通知
你: 登录公司后台，把昨天的数据导出成 CSV
你: 打开这个页面 https://xxx.com，找到价格最低的那个商品截图给我
你: 帮我把这份表单填一下，数据在 ~/data/info.json
```

Agent 会自己完成：打开 Chrome → 导航到目标页面 → 读取页面内容 → 点击、输入、截图 → 返回结果。

### 后台模式

默认 Agent 操作 Chrome 时在后台静默进行（不弹窗、不抢焦点）。如果你想**看着 Agent 操作**：

```
/chrome background off
```

恢复后台模式：

```
/chrome background on
```

### 排查问题

```
/chrome doctor     # 全面诊断：连接状态、扩展版本、权限检测
/chrome status     # 一行摘要：连接 + 授权 + 后台状态
/chrome onboard    # 重新显示扩展安装指引
```

## Agent 工具清单

插件提供 **21 个 `chrome_*` 工具**，Agent 会根据任务自动选择使用：

| 工具 | 用途 |
|------|------|
| `chrome_navigate` | 打开/跳转到指定 URL |
| `chrome_snapshot` | 获取页面可交互元素快照（带唯一 uid） |
| `chrome_click` | 点击页面元素（按钮、链接等） |
| `chrome_type` | 在输入框中输入文字 |
| `chrome_fill` | 批量填写表单 |
| `chrome_find` | 按文本/选择器查找元素 |
| `chrome_inspect` | 查看某个元素的详细信息 |
| `chrome_evaluate` | 在页面中执行 JavaScript |
| `chrome_screenshot` | 截取页面截图 |
| `chrome_scroll` | 滚动页面（触发懒加载等） |
| `chrome_wait_for` | 等待元素出现后再操作 |
| `chrome_hover` | 鼠标悬停 |
| `chrome_drag` | 拖拽元素 |
| `chrome_tap` | 触摸点击（移动端模拟） |
| `chrome_key` | 发送键盘事件（快捷键等） |
| `chrome_upload_file` | 上传本地文件 |
| `chrome_tab` | 标签页管理（打开/关闭/切换） |
| `chrome_list_console_messages` | 读取浏览器控制台输出 |
| `chrome_list_network_requests` | 查看网络请求列表 |
| `chrome_get_network_request` | 查看某个请求的详细信息 |

## 配置参考

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HERMES_CHROME_AUTHORIZE` | 不设置 | 免命令授权（web-UI 等无命令行的环境用） |
| `HERMES_CHROME_BRIDGE_HOST` | `127.0.0.1` | 桥接服务监听地址 |
| `HERMES_CHROME_BRIDGE_PORT` | `16319` | 桥接服务端口 |

### 配置文件（`~/.hermes/config.yaml`）

```yaml
# 永久授权（无需每次 /chrome authorize）
hermes_chrome_plugin:
  authorize: indefinite
```

## 工作原理

```
  Hermes Agent                        Chrome 浏览器
  ┌─────────────────┐     HTTP        ┌──────────────────────┐
  │ hermes-chrome-  │◄───127.0.0.1───│ Hermes Chrome        │
  │ plugin (Python) │    :16319       │ Connector (扩展)     │
  │                 │                 │  ├ service_worker.js │
  │ bridge.py       │                 │  └ snapshot_injected │
  │ tools.py        │                 │    .js (页面注入)    │
  └─────────────────┘                 └──────────────────────┘
```

- Agent 对话 → Hermes 调用 `chrome_*` 工具 → 工具经 bridge 发 HTTP 到 Chrome 扩展
- 扩展通过 Chrome DevTools Protocol 操控页面（点击、输入、读取、截图）
- 所有通信仅走 `127.0.0.1` 本地回环，数据不出机器

## License

MIT
