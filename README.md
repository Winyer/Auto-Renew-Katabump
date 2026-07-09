# Auto Renew Katabump - 修复版

基于 liveqte/Auto-Renew-Katabump 修复，参考 eooce/katabump-renew 改进。

## 修复内容

1. **移除 `--remote-debugging-port=9222`** — 修复了 Chrome 崩溃问题（原代码中固定端口冲突导致 Chrome 进程崩溃，堆栈跟踪 `#0 0x56191311841a <unknown>`）
2. **移除 `use_subprocess=True`** — 解决了 undetected_chromedriver 子进程不稳定问题
3. **改用 seleniumbase 的 `uc_open_with_reconnect`** — 自动处理 Cloudflare 验证页面
4. **JS `nativeInputValueSetter` 填写表单** — 绕过 React 受控组件输入检测
5. **多策略元素定位** — 支持多种 CSS 选择器 + 文本匹配查找 "See" 链接
6. **xdotool 物理鼠标点击** — 绕过 Selenium 自动化检测，用于 Turnstile/ALTCHA 验证
7. **改进 ALTCHA 验证** — 3 种策略：xdotool 物理点击 + Selenium 点击 + JS 强制点击

## 使用方法

### 1. Fork 仓库到你的 GitHub

### 2. 配置 Secrets

在 GitHub 仓库的 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `ACCOUNTS` | 账号信息，格式：`user1:pass1,user2:pass2` |
| `BOT_TOKEN` | (可选) Telegram Bot Token |
| `CHAT_ID` | (可选) Telegram Chat ID |
| `NODE_LINK` | (可选) 代理节点链接 |

### 3. 手动触发

进入 Actions → Auto Renew Katabump → Run workflow

### 4. 本地测试

```bash
# 安装依赖
pip install seleniumbase requests

# 运行（Linux 需要 xvfb）
export ACCOUNTS="your_email:your_password"
xvfb-run python renew_katabump.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACCOUNTS` | - | (必填) 账号信息，user1:pass1,user2:pass2 |
| `HEADLESS` | `true` | 无头模式 |
| `BOT_TOKEN` | - | (可选) Telegram Bot Token |
| `CHAT_ID` | - | (可选) Telegram Chat ID |
| `HTTP_PROXY` | - | (可选) HTTP 代理 |
| `PAUSE_BETWEEN_ACCOUNTS_MS` | `10000` | 多账号间等待时间(毫秒) |

## 工作流程

1. 登录到 dashboard.katabump.com
2. 自动处理 Cloudflare Turnstile 验证
3. 进入服务器详情页
4. 检查是否已到续期日期
5. 点击 Renew 按钮
6. 处理 ALTCHA 人机验证
7. 提交续期并验证结果
8. 发送 Telegram 通知（已配置时）