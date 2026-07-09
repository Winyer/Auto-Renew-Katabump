#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Renew Katabump - 基于 eooce/katabump-renew 模式重写
========================================================
关键修复: 使用 `with SB(...) as sb:` 上下文管理器
(旧代码 `sb = SB(); sb.__enter__()` 会导致 _GeneratorContextManager 无 driver 属性)

依赖: pip install seleniumbase requests
系统: Linux (推荐, 需要 xvfb, xdotool) / Windows (本地测试用)
"""

import os
import sys
import re
import time
import random
import subprocess
import logging
import requests
from datetime import datetime, timezone, timedelta

try:
    from seleniumbase import SB
except ImportError:
    print("请先安装 seleniumbase: pip install seleniumbase")
    exit(1)

# ===================== 平台检测 =====================
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
HAS_XDOTOOL = False
if IS_LINUX:
    try:
        subprocess.run(["xdotool", "--version"], capture_output=True, timeout=3)
        HAS_XDOTOOL = True
    except Exception:
        HAS_XDOTOOL = False

# ===================== 配置日志 =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ===================== 全局配置 =====================
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
PAUSE_BETWEEN_ACCOUNTS_MS = int(os.getenv("PAUSE_BETWEEN_ACCOUNTS_MS", "10000"))
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID", "")
ACCOUNTS_ENV = os.getenv("ACCOUNTS", "")
PROXY_SERVER = os.getenv("HTTP_PROXY", "")
# If setup_proxy.sh set IS_PROXY=true, use local sing-box SOCKS5 proxy
if os.getenv("IS_PROXY", "false").lower() == "true":
    PROXY_SERVER = "socks5://127.0.0.1:1080"

BASE_URL = "https://dashboard.katabump.com"

# ===================== JS 脚本常量 =====================

# 检查 Turnstile 是否已通过
_SOLVED_TS_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

# 检查 Turnstile 是否存在
_EXISTS_TS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

# 获取 Turnstile iframe 坐标（用于 xdotool 物理点击）
_COORDS_TS_JS = """
(function(){
    var iframes = document.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var src = iframes[i].src || '';
        if (src.includes('cloudflare') || src.includes('turnstile') || src.includes('challenges')) {
            var r = iframes[i].getBoundingClientRect();
            if (r.width > 0 && r.height > 0)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
        }
    }
    var inp = document.querySelector('input[name="cf-turnstile-response"]');
    if (inp) {
        var p = inp.parentElement;
        for (var j = 0; j < 5; j++) {
            if (!p) break;
            var r = p.getBoundingClientRect();
            if (r.width > 100 && r.height > 30)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
            p = p.parentElement;
        }
    }
    return null;
})()
"""

# 获取窗口信息（用于 xdotool 坐标计算）
_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# 展开 Turnstile iframe（使隐藏的可见）
_EXPAND_TS_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

# 展开 ALTCHA iframe（模态框内），同时返回坐标
_ALTCHA_EXPAND_JS = """
(function() {
    var modal = document.querySelector('div.modal.show') || document;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var r = iframes[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            iframes[i].style.width  = '300px';
            iframes[i].style.height = '150px';
            iframes[i].style.minWidth  = '300px';
            iframes[i].style.minHeight = '150px';
            iframes[i].style.visibility = 'visible';
            iframes[i].style.opacity = '1';
            var el = iframes[i];
            for (var j = 0; j < 10; j++) {
                el = el.parentElement;
                if (!el) break;
                el.style.overflow = 'visible';
            }
            var r2 = iframes[i].getBoundingClientRect();
            return { cx: Math.round(r2.x + 30), cy: Math.round(r2.y + r2.height / 2) };
        }
    }
    return null;
})()
"""

# 检测 ALTCHA 是否已验证通过
_ALTCHA_SOLVED_JS = """
(function(){
    var modal = document.querySelector('div.modal.show') || document;
    var inputs = modal.querySelectorAll('input[type="hidden"]');
    for (var i = 0; i < inputs.length; i++) {
        var n = (inputs[i].name || '').toLowerCase();
        if ((n.includes('altcha') || n.includes('captcha')) &&
            inputs[i].value && inputs[i].value.length > 20) return true;
    }
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < cbs.length; j++) {
        if (cbs[j].disabled) return true;
    }
    var w = modal.querySelector('[data-state="verified"],.altcha--verified,.altcha-verified');
    if (w) return true;
    return false;
})()
"""

# JS 强制点击 ALTCHA 复选框
_ALTCHA_FORCE_CLICK_JS = """
(function(){
    var modal = document.querySelector('div.modal.show');
    if (!modal) return;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        iframes[i].click();
        iframes[i].dispatchEvent(new MouseEvent('click', {bubbles:true}));
    }
    var labels = modal.querySelectorAll('label');
    for (var j = 0; j < labels.length; j++) {
        var txt = (labels[j].textContent || '').toLowerCase();
        if (txt.includes('robot') || txt.includes('captcha') || txt.includes('verify'))
            labels[j].click();
    }
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var k = 0; k < cbs.length; k++) {
        if (!cbs[k].disabled) {
            cbs[k].click();
            cbs[k].dispatchEvent(new MouseEvent('click', {bubbles:true}));
        }
    }
})()
"""


# ===================== 工具函数 =====================

def mask_email(email):
    """邮箱脱敏"""
    try:
        if "@" in email:
            prefix, domain = email.split("@")
            if len(prefix) <= 2:
                return f"{prefix[0]}***@{domain}"
            return f"{prefix[0]}***{prefix[-1]}@{domain}"
        return f"{email[0]}***{email[-1]}" if len(email) > 2 else email
    except Exception:
        return "UnknownUser"


def send_telegram(message, screenshot_path=None):
    """发送 Telegram 通知"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    tz_offset = timezone(timedelta(hours=8))
    time_str = datetime.now(tz_offset).strftime("%Y-%m-%d %H:%M:%S") + " HKT"
    full_message = f"Katabump 续期通知\n\n续期时间：{time_str}\n\n{message}"
    try:
        if screenshot_path and os.path.exists(screenshot_path):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            with open(screenshot_path, "rb") as photo:
                requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": full_message},
                    files={"photo": photo},
                    timeout=20,
                )
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(
                url, data={"chat_id": TELEGRAM_CHAT_ID, "text": full_message}, timeout=10
            )
        logger.info("Telegram 通知发送成功")
    except Exception as e:
        logger.warning(f"Telegram 发送失败: {e}")


def js_fill_input(sb, selector, text):
    """使用 JS nativeInputValueSetter 填写 React 表单输入框"""
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
    sb.execute_script(
        f"""
    (function() {{
        var el = document.querySelector('{selector}');
        if (!el) return;
        try {{
            var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, "value"
            ).set;
            if (nativeInputValueSetter) {{
                nativeInputValueSetter.call(el, "{safe_text}");
            }} else {{
                el.value = "{safe_text}";
            }}
        }} catch(e) {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """
    )


def _activate_window():
    """激活 Chrome 窗口（Linux xdotool）"""
    if not HAS_XDOTOOL:
        return
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", cls],
                capture_output=True, text=True, timeout=3,
            )
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wids[0]],
                    timeout=3, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.2)
                return
        except Exception:
            pass


def _xdotool_click(x, y):
    """使用 xdotool 进行物理鼠标点击（仅 Linux）"""
    if not HAS_XDOTOOL:
        return False
    _activate_window()
    try:
        subprocess.run(
            ["xdotool", "mousemove", "--sync", str(x), str(y)],
            timeout=3, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _actionchains_click(sb, coords, context=""):
    """通过 ActionChains 偏移点击（Windows/Linux 通用后备方案）"""
    from selenium.webdriver.common.action_chains import ActionChains

    try:
        actions = ActionChains(sb.driver)
        actions.move_by_offset(coords["cx"], coords["cy"])
        actions.pause(random.uniform(0.2, 0.4))
        actions.click()
        actions.perform()
        logger.info(f"[{context}] ActionChains 点击 ({coords['cx']}, {coords['cy']})")
        return True
    except Exception as e:
        logger.debug(f"ActionChains 点击失败: {e}")
        return False


# ===================== Turnstile 处理 =====================

def _handle_turnstile(sb, masked_user, context=""):
    """处理 Cloudflare Turnstile 验证"""
    logger.info(f"[{masked_user}] [{context}] 处理 Turnstile 验证...")

    # 先等待自动通过（最多 30 秒）
    for i in range(60):
        if sb.execute_script(_SOLVED_TS_JS):
            logger.info(f"[{masked_user}] [{context}] Turnstile 自动通过 ({(i+1)*0.5:.1f}s)")
            return True
        time.sleep(0.5)

    # 展开隐藏的 Turnstile
    for _ in range(3):
        try:
            sb.execute_script(_EXPAND_TS_JS)
        except Exception:
            pass
        time.sleep(0.5)

    # 尝试切换到 Turnstile iframe 内部点击 checkbox
    try:
        iframes = sb.find_elements("iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenges.cloudflare.com" in src or "turnstile" in src:
                sb.driver.switch_to.frame(iframe)
                time.sleep(1)
                # 在 iframe 内部找 checkbox 并点击
                try:
                    checkbox = sb.driver.find_element("css selector", 'input[type="checkbox"]')
                    checkbox.click()
                    logger.info(f"[{masked_user}] [{context}] iframe内点击checkbox")
                    time.sleep(3)
                except Exception:
                    # 尝试点击 body
                    try:
                        sb.driver.find_element("css selector", "body").click()
                        logger.info(f"[{masked_user}] [{context}] iframe内点击body")
                        time.sleep(3)
                    except Exception:
                        pass
                sb.driver.switch_to.default_content()
                break
    except Exception:
        try:
            sb.driver.switch_to.default_content()
        except Exception:
            pass

    if sb.execute_script(_SOLVED_TS_JS):
        logger.info(f"[{masked_user}] [{context}] iframe点击后 Turnstile 通过")
        return True

    # 尝试通过 JS 直接点击 Turnstile iframe
    try:
        sb.execute_script("""
        (function() {
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('challenges.cloudflare.com') || src.includes('turnstile')) {
                    iframes[i].click();
                    return true;
                }
            }
            return false;
        })()
        """)
    except Exception:
        pass
    time.sleep(3)

    if sb.execute_script(_SOLVED_TS_JS):
        logger.info(f"[{masked_user}] [{context}] JS点击后 Turnstile 通过")
        return True

    # 尝试 Selenium 点击 Turnstile checkbox
    try:
        checkbox = sb.execute_script("""
        (function() {
            var iframes = document.querySelectorAll('iframe');
            for (var i = 0; i < iframes.length; i++) {
                var src = iframes[i].src || '';
                if (src.includes('challenges.cloudflare.com') || src.includes('turnstile')) {
                    var r = iframes[i].getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height};
                }
            }
            return null;
        })()
        """)
        if checkbox and checkbox.get('w', 0) > 0:
            from selenium.webdriver.common.action_chains import ActionChains
            actions = ActionChains(sb.driver)
            actions.move_by_offset(checkbox['x'], checkbox['y'])
            actions.pause(0.3)
            actions.click()
            actions.perform()
            logger.info(f"[{masked_user}] [{context}] ActionChains 点击 Turnstile ({checkbox['x']}, {checkbox['y']})")
            time.sleep(3)
    except Exception:
        pass

    if sb.execute_script(_SOLVED_TS_JS):
        logger.info(f"[{masked_user}] [{context}] ActionChains 点击后 Turnstile 通过")
        return True

    # xdotool 仅在有 xvfb 的环境下使用
    if HAS_XDOTOOL:
        coords = None
        try:
            coords = sb.execute_script(_COORDS_TS_JS)
        except Exception:
            pass

        if coords:
            for attempt in range(3):
                try:
                    wi = sb.execute_script(_WININFO_JS)
                    bar = wi["oh"] - wi["ih"]
                    ax = coords["cx"] + wi["sx"]
                    ay = coords["cy"] + wi["sy"] + bar
                    logger.info(f"[{masked_user}] [{context}] xdotool 点击 ({ax}, {ay})")
                    _xdotool_click(ax, ay)
                    for _ in range(8):
                        time.sleep(0.5)
                        if sb.execute_script(_SOLVED_TS_JS):
                            logger.info(f"[{masked_user}] [{context}] xdotool 第{attempt+1}次通过")
                            return True
                except Exception:
                    pass

    logger.warning(f"[{masked_user}] [{context}] Turnstile 未通过，将尝试直接提交")
    return False


# ===================== ALTCHA 处理 =====================

def _handle_altcha(sb, masked_user):
    """处理 ALTCHA 验证（模态框内）"""
    logger.info(f"[{masked_user}] 处理 ALTCHA 验证...")
    time.sleep(2)

    # 检查是否已自动通过
    if sb.execute_script(_ALTCHA_SOLVED_JS):
        logger.info(f"[{masked_user}] ALTCHA 已自动通过")
        return True

    # 获取 ALTCHA iframe 坐标
    coords = None
    try:
        coords = sb.execute_script(_ALTCHA_EXPAND_JS)
    except Exception:
        pass

    if coords:
        logger.info(f"[{masked_user}] ALTCHA iframe 坐标: ({coords['cx']}, {coords['cy']})")

    # 最多尝试 3 轮
    for attempt in range(3):
        if sb.execute_script(_ALTCHA_SOLVED_JS):
            logger.info(f"[{masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
            return True

        # 策略 1: xdotool 物理点击
        clicked = False
        if coords and HAS_XDOTOOL:
            try:
                wi = sb.execute_script(_WININFO_JS)
                bar = wi["oh"] - wi["ih"]
                ax = coords["cx"] + wi["sx"]
                ay = coords["cy"] + wi["sy"] + bar
                logger.info(f"[{masked_user}] ALTCHA xdotool 点击 ({ax}, {ay})")
                clicked = _xdotool_click(ax, ay)
            except Exception:
                pass

        # 策略 1b: ActionChains 偏移点击
        if not clicked and coords:
            _actionchains_click(sb, coords, context=f"{masked_user} ALTCHA")
            clicked = True

        if clicked:
            for _ in range(6):
                time.sleep(1)
                if sb.execute_script(_ALTCHA_SOLVED_JS):
                    logger.info(f"[{masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
                    return True

        # 策略 2: JS 强制点击
        try:
            sb.execute_script(_ALTCHA_FORCE_CLICK_JS)
            logger.info(f"[{masked_user}] ALTCHA JS 强制点击")
        except Exception:
            pass

        for _ in range(6):
            time.sleep(1)
            if sb.execute_script(_ALTCHA_SOLVED_JS):
                logger.info(f"[{masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
                return True

        logger.info(f"[{masked_user}] ALTCHA 第{attempt+1}轮未通过，重试...")
        # 重新获取坐标
        try:
            new_coords = sb.execute_script(_ALTCHA_EXPAND_JS)
            if new_coords:
                coords = new_coords
        except Exception:
            pass

    logger.error(f"[{masked_user}] ALTCHA 3 轮均失败")
    return False


# ===================== 业务逻辑函数 =====================

def login(sb, email, password):
    """登录到 Katabump 面板（含 Turnstile 处理）"""
    masked = mask_email(email)
    logger.info(f"[{masked}] 开始登录")

    # 打开登录页面（UC 模式用 uc_open_with_reconnect，普通模式用 sb.open）
    logger.info(f"[{masked}] 打开登录页面: {BASE_URL}/auth/login")
    use_uc = os.environ.get("USE_UC", "true" if IS_LINUX else "false").lower() == "true"
    if use_uc:
        try:
            sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=4)
        except Exception:
            sb.open(BASE_URL + "/auth/login")
    else:
        sb.open(BASE_URL + "/auth/login")
    time.sleep(4)

    # UC 模式下使用 uc_gui_click_captcha 自动处理 Cloudflare Turnstile
    if use_uc:
        try:
            sb.uc_gui_click_captcha()  # 自动检测并点击 Turnstile
            logger.info(f"[{masked}] uc_gui_click_captcha 已执行")
            time.sleep(3)
        except Exception as e:
            logger.warning(f"[{masked}] uc_gui_click_captcha 失败: {e}")

    # 等待 Cloudflare 验证通过（最多 30 秒）
    logger.info(f"[{masked}] 等待 Cloudflare 验证通过...")
    cf_passed = False
    for i in range(30):
        page_src = sb.get_page_source() or ""
        if "input" in page_src.lower() and (
            "email" in page_src.lower() or "password" in page_src.lower()
        ):
            cf_passed = True
            logger.info(f"[{masked}] Cloudflare 验证通过 ({i+1}s)")
            break
        time.sleep(1)

    if not cf_passed:
        logger.warning(f"[{masked}] Cloudflare 验证可能未通过，继续尝试...")

    # 等待登录表单出现
    try:
        sb.wait_for_element('input#email, input[name="email"]', timeout=15)
    except Exception:
        try:
            sb.wait_for_element("input#Email, input[name=\"Email\"]", timeout=5)
        except Exception:
            logger.error(f"[{masked}] 页面未加载出登录表单")
            try:
                sb.save_screenshot(f"login_fail_{masked}.png")
            except Exception:
                pass
            return False

    # 等待 Turnstile 在页面加载后自动完成（最多 15 秒）
    # UC 模式下 Turnstile 经常自动静默通过
    ts_auto = False
    if sb.execute_script(_EXISTS_TS_JS):
        logger.info(f"[{masked}] 检测到 Turnstile，等待自动通过...")
        for i in range(15):
            if sb.execute_script(_SOLVED_TS_JS):
                ts_auto = True
                logger.info(f"[{masked}] Turnstile 自动通过 ({i+1}s)")
                break
            time.sleep(1)
        if not ts_auto:
            logger.warning(f"[{masked}] Turnstile 未自动通过，将在填表后处理")
    else:
        logger.info(f"[{masked}] 未检测到 Turnstile")

    # 关闭 Cookie 弹窗
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    # 填写邮箱
    logger.info(f"[{masked}] 填写用户名/邮箱...")
    js_fill_input(sb, 'input#email, input[name="email"]', email)
    time.sleep(0.5 + random.random() * 0.5)

    # 填写密码
    logger.info(f"[{masked}] 填写密码...")
    js_fill_input(sb, 'input#password, input[name="password"]', password)
    time.sleep(0.5 + random.random() * 0.5)

    # 如果 Turnstile 未自动通过，尝试手动处理
    if not ts_auto and sb.execute_script(_EXISTS_TS_JS):
        # 先用 uc_gui_click_captcha 尝试（seleniumbase 内置的 PyAutoGUI 点击）
        try:
            sb.uc_gui_click_captcha()
            logger.info(f"[{masked}] 填表后 uc_gui_click_captcha 已执行")
            time.sleep(3)
        except Exception as e:
            logger.warning(f"[{masked}] 填表后 uc_gui_click_captcha 失败: {e}")

        if sb.execute_script(_SOLVED_TS_JS):
            logger.info(f"[{masked}] uc_gui_click_captcha 后 Turnstile 通过")
            ts_auto = True

        if not ts_auto and not _handle_turnstile(sb, masked, "Login Auth"):
            # Turnstile 失败不直接退出，尝试直接提交（有时提交时会自动验证）
            logger.warning(f"[{masked}] Turnstile 验证未通过，尝试直接提交...")

    # 提交登录（回车键）
    logger.info(f"[{masked}] 提交登录...")
    sb.press_keys('input#password, input[name="password"]', "\n")

    # 等待登录跳转
    logger.info(f"[{masked}] 等待登录跳转...")
    for _ in range(15):
        time.sleep(1)
        cur_url = sb.get_current_url().split("?")[0].lower()
        page_title = sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard" in page_title:
            break

    cur_url = sb.get_current_url().split("?")[0].lower()
    page_title = sb.get_title() or ""
    if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard" in page_title:
        logger.info(f"[{masked}] 登录成功！")
        return True

    logger.error(f"[{masked}] 登录失败 (URL: {cur_url})")
    try:
        sb.save_screenshot(f"login_fail_{masked}.png")
    except Exception:
        pass
    return False


def _goto_server_detail(sb, masked_user):
    """从 Dashboard 进入服务器详情页"""
    logger.info(f"[{masked_user}] 进入服务器详情页...")
    time.sleep(5)

    # 检查是否有"还无法续期"的提示
    try:
        alert_el = sb.find_element("div.alert", timeout=4)
        alert_text = (alert_el.text or "").strip()
        if alert_text and "can't renew" in alert_text.lower():
            logger.info(f"[{masked_user}] 页面提示: {alert_text}")
            return True, "skip", alert_text
    except Exception:
        pass

    # 多策略查找 "See" 链接
    see_link = None
    selectors = [
        'a[href*="/servers/edit?id="]',
        'td a[href*="/servers/edit"]',
        'table a[href*="/servers/edit"]',
        "table td a",
    ]

    for sel in selectors:
        try:
            see_link = sb.find_element(sel, timeout=8)
            logger.info(f"[{masked_user}] 通过选择器找到链接: {sel}")
            break
        except Exception:
            continue

    # 选择器失败，尝试文本匹配
    if see_link is None:
        logger.info(f"[{masked_user}] 选择器未命中，尝试文本匹配...")
        try:
            for a in sb.find_elements("a"):
                if (a.text or "").strip().lower() == "see":
                    see_link = a
                    logger.info(f"[{masked_user}] 通过文本 'See' 找到链接")
                    break
        except Exception:
            pass

    if see_link is None:
        logger.error(f"[{masked_user}] 未找到 'See' 链接")
        try:
            sb.save_screenshot(f"no_see_link_{masked_user}.png")
        except Exception:
            pass
        return False, None, None

    # 点击 See 链接
    logger.info(f"[{masked_user}] 点击 'See' 进入服务器详情页...")
    try:
        see_link.click()
    except Exception:
        sb.execute_script("arguments[0].click();", see_link)
    time.sleep(5)
    return True, None, None


def _open_renew_modal(sb, masked_user):
    """点击 Renew 按钮，打开续期模态框"""
    logger.info(f"[{masked_user}] 查找 Renew 按钮...")

    renew_btn = None
    try:
        renew_btn = sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
    except Exception:
        try:
            renew_btn = sb.find_element("button.btn.btn-outline-primary", timeout=5)
        except Exception:
            logger.error(f"[{masked_user}] 未找到 Renew 按钮")
            return False

    # 滚动到按钮并点击
    sb.execute_script("arguments[0].scrollIntoView({block: 'center'});", renew_btn)
    time.sleep(0.8)
    try:
        renew_btn.click()
    except Exception:
        sb.execute_script("arguments[0].click();", renew_btn)
    logger.info(f"[{masked_user}] 已点击 Renew 按钮")
    time.sleep(3)

    # 确认模态框已弹出
    try:
        sb.find_element("div.modal.show", timeout=5)
        logger.info(f"[{masked_user}] Renew 模态框已弹出")
    except Exception:
        logger.warning(f"[{masked_user}] 模态框未弹出，尝试继续...")

    return True


def _submit_renew(sb, masked_user):
    """点击最终 Renew 提交按钮并验证结果"""
    logger.info(f"[{masked_user}] 点击提交 Renew...")

    # 读取当前到期时间
    initial_expiry = ""
    try:
        expiry_el = sb.find_element(
            "//div[contains(text(), 'Expiry')]/following-sibling::div",
            timeout=10, by="xpath",
        )
        initial_expiry = expiry_el.text.strip()
        logger.info(f"[{masked_user}] 当前到期时间: {initial_expiry}")
    except Exception:
        logger.warning(f"[{masked_user}] 无法读取初始时间")

    # 点击提交按钮
    try:
        submit_btn = sb.find_element(
            'div.modal.show button.btn-primary, div.modal.show button[type="submit"]',
            timeout=5,
        )
        submit_btn.click()
    except Exception:
        sb.execute_script(
            """
            (function(){
                var m = document.querySelector('div.modal.show');
                if (!m) return;
                var bs = m.querySelectorAll('button');
                for (var i = 0; i < bs.length; i++)
                    if (/renew/i.test(bs[i].textContent)) bs[i].click();
            })()
        """
        )

    logger.info(f"[{masked_user}] 等待续期结果...")
    time.sleep(7 + random.random() * 3)

    # 核验结果
    try:
        alerts = sb.find_elements("div.alert-danger, div.alert")
        if alerts:
            alert_text = (alerts[0].text or "").strip().replace("x", "")
            if alert_text:
                logger.info(f"[{masked_user}] 页面提示: {alert_text}")
                low = alert_text.lower()
                if any(kw in low for kw in ["renewed", "success", "extended"]):
                    return True, f"续期成功: {alert_text}"
                elif "can't renew" in low or "unable" in low:
                    return False, f"未到续期时间: {alert_text}"
                else:
                    return False, alert_text

        # 检查到期时间是否已更新
        try:
            final_expiry_el = sb.find_element(
                "//div[contains(text(), 'Expiry')]/following-sibling::div",
                timeout=5, by="xpath",
            )
            final_expiry = final_expiry_el.text.strip()
            logger.info(f"[{masked_user}] 续期后到期时间: {final_expiry}")
            if final_expiry != initial_expiry and len(final_expiry) > 0:
                return True, f"续期成功: {final_expiry}"
            else:
                return False, f"到期时间未更新 ({initial_expiry})"
        except Exception:
            return False, "无法获取续期结果"
    except Exception as e:
        return False, f"验证结果出错: {e}"


# ===================== 单账号续期流程 =====================

def renew_account(sb, email, password):
    """
    单个账号的完整续期流程（含重试）。
    返回 (success: bool, message: str, screenshot_path: str|None)
    """
    masked = mask_email(email)
    max_retries = 3
    last_error = ""
    screenshot_path = None

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"[{masked}] 正在进行第 {attempt+1} 次尝试...")
                try:
                    sb.driver.refresh()
                except Exception:
                    pass
                time.sleep(5 + random.random() * 3)

            # 1. 登录
            if not login(sb, email, password):
                last_error = "登录失败"
                continue

            # 2. 进入服务器详情页
            detail_ok, skip_flag, skip_msg = _goto_server_detail(sb, masked)
            if not detail_ok:
                last_error = "无法进入服务器详情页"
                continue

            if skip_flag == "skip":
                return True, skip_msg, None

            # 3. 打开续期模态框
            if not _open_renew_modal(sb, masked):
                last_error = "未找到 Renew 按钮"
                continue

            # 4. 处理 ALTCHA 验证
            altcha_ok = _handle_altcha(sb, masked)
            if not altcha_ok:
                logger.warning(f"[{masked}] ALTCHA 验证未完全通过，仍尝试提交...")

            # 5. 提交续期
            success, message = _submit_renew(sb, masked)
            if success:
                return True, message, None
            else:
                last_error = message
                if "未到续期时间" in message:
                    return True, message, None
                break

        except Exception as e:
            last_error = f"异常: {str(e)[:100]}"
            logger.error(f"[{masked}] 第 {attempt+1} 次执行出错: {e}")

        if attempt < max_retries - 1:
            time.sleep(5 + random.random() * 5)

    # 最终失败，截图
    screenshot_file = f"error-{email.split('@')[0]}.png"
    try:
        sb.save_screenshot(screenshot_file)
        screenshot_path = screenshot_file
    except Exception:
        pass
    return False, f"历经 {max_retries} 次尝试仍失败: {last_error}", screenshot_path


# ===================== 入口 =====================

def main():
    if not ACCOUNTS_ENV:
        logger.error("未配置账号（ACCOUNTS 环境变量）")
        print("请设置环境变量 ACCOUNTS，格式: user1:pass1,user2:pass2")
        exit(1)

    # 解析账号列表
    raw_accs = re.split(r"[,;]", ACCOUNTS_ENV)
    accounts = []
    for a in raw_accs:
        a = a.strip()
        if ":" in a:
            u, p = a.split(":", 1)
            accounts.append({"user": u.strip(), "pass": p.strip()})
        elif a:
            logger.warning(f"跳过无效账号格式: {a}")

    if not accounts:
        logger.error("无有效账号")
        exit(1)

    # USE_UC: Linux 默认 true，Windows 默认 false
    use_uc = os.environ.get("USE_UC", "true" if IS_LINUX else "false").lower() == "true"

    # 构建 SB 参数
    sb_kwargs = {
        "uc": use_uc,
        "headless": HEADLESS,
        "chromium_arg": "--no-first-run --disable-extensions --disable-gpu --no-sandbox --disable-dev-shm-usage",
    }
    if PROXY_SERVER:
        sb_kwargs["proxy"] = PROXY_SERVER

    total = len(accounts)
    logger.info(f"Katabump 自动续期脚本启动")
    logger.info(f"账号数: {total}")
    logger.info(f"无头模式: {HEADLESS}")
    logger.info(f"UC 模式: {use_uc}")
    logger.info(f"平台: {'Windows' if IS_WINDOWS else 'Linux'}")
    if PROXY_SERVER:
        logger.info(f"代理: {PROXY_SERVER}")

    # =====================================================
    # 关键: 使用 with SB(...) as sb: 上下文管理器
    # 绝不能用 sb = SB(); sb.__enter__()
    # =====================================================
    with SB(**sb_kwargs) as sb:
        # 设置窗口大小
        try:
            sb.driver.set_window_size(1280, 800)
        except Exception:
            pass

        # 显示出口 IP
        try:
            sb.open("https://api.ip.sb/ip")
            ip_text = sb.get_text("body").strip()
            logger.info(f"当前出口 IP: {ip_text}")
        except Exception:
            pass

        # 逐个账号处理
        results = []
        last_screenshot = None
        success_count = 0

        for i, acc in enumerate(accounts):
            logger.info(f"\n{'='*50}")
            logger.info(f"处理第 {i+1}/{total} 个账号: {mask_email(acc['user'])}")
            logger.info(f"{'='*50}")

            success, msg, screenshot = renew_account(sb, acc["user"], acc["pass"])
            results.append({"message": msg, "success": success})
            if success:
                success_count += 1
            if screenshot:
                last_screenshot = screenshot

            if i < total - 1:
                wait_time = PAUSE_BETWEEN_ACCOUNTS_MS + random.random() * 5000
                logger.info(f"账号间歇期：等待 {round(wait_time/1000)} 秒...")
                time.sleep(wait_time / 1000)

        # 汇总结果并发送 Telegram 通知
        summary_lines = []
        for j, r in enumerate(results):
            summary_lines.append(f"账号{j+1}: {r['message']}")
        summary = f"续期汇总: {success_count}/{total} 个账号成功\n\n" + "\n\n".join(summary_lines)
        send_telegram(summary, last_screenshot)

        # 清理截图
        import glob
        for f in glob.glob("error-*.png"):
            try:
                os.remove(f)
            except Exception:
                pass

        logger.info(f"\n所有账号处理完成！{success_count}/{total} 成功")

    # with 块结束，SB 自动清理（driver 退出、chromedriver 关闭等）
    logger.info("浏览器已自动关闭")


if __name__ == "__main__":
    main()
