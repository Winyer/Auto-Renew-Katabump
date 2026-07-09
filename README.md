# Auto Renew Katabump - Fixed

Based on liveqte/Auto-Renew-Katabump with fixes, inspired by eooce/katabump-renew.

## Fixes

1. Removed --remote-debugging-port=9222 - Fixed Chrome crash
2. Removed use_subprocess=True - Fixed undetected_chromedriver instability
3. Switched to seleniumbase uc_open_with_reconnect - Auto Cloudflare bypass
4. JS 
ativeInputValueSetter for form filling - Bypasses React controlled inputs
5. Multi-strategy element locating - Multiple CSS selectors + text matching
6. xdotool physical mouse click - Bypasses Selenium automation detection
7. Improved ALTCHA handling - 3 strategies: xdotool + Selenium click + JS click

## Usage

### 1. Fork this repository

### 2. Configure Secrets

Go to Settings > Secrets and variables > Actions, add:

| Secret | Description |
|--------|-------------|
| ACCOUNTS | Account info: user1:pass1,user2:pass2 |
| BOT_TOKEN | (Optional) Telegram Bot Token |
| CHAT_ID | (Optional) Telegram Chat ID |
| NODE_LINK | (Optional) Proxy node link |

### 3. Manual trigger

Go to Actions > Auto Renew Katabump > Run workflow

### 4. Local test

`ash
pip install seleniumbase requests
export ACCOUNTS="your_email:your_password"
xvfb-run python renew_katabump.py
`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| ACCOUNTS | - | (Required) Account info |
| HEADLESS | 	rue | Headless mode |
| BOT_TOKEN | - | (Optional) Telegram Bot Token |
| CHAT_ID | - | (Optional) Telegram Chat ID |
| HTTP_PROXY | - | (Optional) HTTP proxy |
| PAUSE_BETWEEN_ACCOUNTS_MS | 10000 | Wait between accounts (ms) |