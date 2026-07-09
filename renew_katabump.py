#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Renew Katabump - 修复版 (2026-07-09)
=============================================
基于 liveqte/Auto-Renew-Katabump 修复，参考 eooce/katabump-renew 改进

修复内容:
1. 移除 --remote-debugging-port=9222 (导致 Chrome 崩溃)
2. 移除 use_subprocess=True (导致不稳定)
3. 改用 seleniumbase 的 uc_open_with_reconnect 自动处理 Cloudflare
4. 使用 JS nativeInputValueSetter 填写 React 表单 (绕过输入检测)
5. 多策略元素定位 (多种选择器 + 文本匹配)
6. 添加 xdotool 物理鼠标点击 (绕过 Selenium 检测)
7. 改进 ALTCHA 验证处理 (3 种策略: xdotool + Selenium click + JS 强制点击)

依赖: pip install seleniumbase requests
系统: Linux (推荐, 需要 xvfb, xdotool) / Windows (本地测试用)

Windows 注意: Windows 没有 xdotool，会自动回退到 ActionChains 点击
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

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
HAS_XDOTOOL = False
if IS_LINUX:
    try:
        subprocess.run(["xdotool", "--version"], capture_output=True, timeout=3)
        HAS_XDOTOOL = True
    except Exception:
        HAS_XDOTOOL = False

try:
    from seleniumbase import SB
except ImportError:
    print("❌ 请先安装 seleniumbase: pip install seleniumbase")
    exit(1)

# ===================== 配置日志 =====================
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===================== 全局配置 =====================
HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
PAUSE_BETWEEN_ACCOUNTS_MS = int(os.getenv('PAUSE_BETWEEN_ACCOUNTS_MS', '10000'))
TELEGRAM_BOT_TOKEN = os.getenv('BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('CHAT_ID', '')
ACCOUNTS_ENV = os.getenv('ACCOUNTS', '')
PROXY_SERVER = os.getenv('HTTP_PROXY', '')

BASE_URL = "https://dashboard.katabump.com"

# ===================== JS 脚本常量 =====================

# 展开 Turnstile iframe
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

# 检查 Turnstile 是否存在
_EXISTS_TS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

# 检查 Turnstile 是否已通过
_SOLVED_TS_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
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

# 展开 ALTCHA iframe（模态框内）
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
            prefix, domain = email.split('@')
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
            with open(screenshot_path, 'rb') as photo:
                requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": full_message},
                              files={'photo': photo}, timeout=20)
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": full_message}, timeout=10)
        logger.info("Telegram 通知发送成功")
    except Exception as e:
        logger.warning(f"Telegram 发送失败: {e}")


def js_fill_input(sb, selector, text):
    """使用 JS nativeInputValueSetter 填写 React 表单输入框"""
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")
    sb.execute_script(f"""
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
    """)


def _activate_window():
    """激活 Chrome 窗口（Linux xdotool，Windows 跳过）"""
    if not HAS_XDOTOOL:
        return
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                               capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]],
                               timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass


def _xdotool_click(x, y):
    """使用 xdotool 进行物理鼠标点击（仅 Linux 可用）"""
    if not HAS_XDOTOOL:
        return False
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)],
                       timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _actionchains_click(sb, coords, context=""):
    """Windows/Linux 通用: 通过 ActionChains 偏移点击"""
    from selenium.webdriver.common.action_chains import ActionChains
    driver = sb.driver
    try:
        actions = ActionChains(driver)
        # 移动到页面左上角再偏移
        actions.move_by_offset(coords["cx"], coords["cy"])
        actions.pause(random.uniform(0.2, 0.4))
        actions.click()
        actions.perform()
        logger.info(f"[{context}] ActionChains 点击 ({coords['cx']}, {coords['cy']})")
        return True
    except Exception as e:
        logger.debug(f"ActionChains 点击失败: {e}")
        return False


# ===================== Katabump 续期核心类 =====================

class KatabumpAutoRenew:
    def __init__(self, user, password):
        self.user = user
        self.password = password
        self.sb = None
        self.screenshot_path = None
        self.masked_user = mask_email(user)

    def _handle_turnstile(self, context=""):
        """处理 Cloudflare Turnstile 验证"""
        logger.info(f"[{self.masked_user}] [{context}] 处理 Turnstile 验证...")
        time.sleep(2)

        if self.sb.execute_script(_SOLVED_TS_JS):
            logger.info(f"[{self.masked_user}] [{context}] Turnstile 已静默通过")
            return True

        # 展开隐藏的 Turnstile
        for _ in range(3):
            try:
                self.sb.execute_script(_EXPAND_TS_JS)
            except Exception:
                pass
            time.sleep(0.5)

        # 最多尝试 6 次点击
        for attempt in range(6):
            if self.sb.execute_script(_SOLVED_TS_JS):
                logger.info(f"[{self.masked_user}] [{context}] Turnstile 通过 (第{attempt+1}次)")
                return True

            try:
                self.sb.execute_script(_EXPAND_TS_JS)
            except Exception:
                pass
            time.sleep(0.3)

            # 获取坐标并点击
            coords = None
            try:
                coords = self.sb.execute_script(_COORDS_TS_JS)
            except Exception:
                pass

            if coords:
                clicked = False
                # 策略 1: Linux 使用 xdotool 物理点击
                if HAS_XDOTOOL:
                    try:
                        wi = self.sb.execute_script(_WININFO_JS)
                        bar = wi["oh"] - wi["ih"]
                        ax = coords["cx"] + wi["sx"]
                        ay = coords["cy"] + wi["sy"] + bar
                        logger.info(f"[{self.masked_user}] [{context}] xdotool 点击 ({ax}, {ay})")
                        clicked = _xdotool_click(ax, ay)
                    except Exception:
                        pass

                # 策略 2: ActionChains 偏移点击（Windows/Linux 通用）
                if not clicked:
                    _actionchains_click(self.sb, coords, context=f"{self.masked_user} [{context}]")

                # 等待验证结果
                for _ in range(8):
                    time.sleep(0.5)
                    if self.sb.execute_script(_SOLVED_TS_JS):
                        logger.info(f"[{self.masked_user}] [{context}] Turnstile 通过 (第{attempt+1}次)")
                        return True

            logger.info(f"[{self.masked_user}] [{context}] 第{attempt+1}次未通过，重试...")

        logger.error(f"[{self.masked_user}] [{context}] Turnstile 6 次均失败")
        return False

    def _handle_altcha(self):
        """处理 ALTCHA 验证（模态框内）"""
        logger.info(f"[{self.masked_user}] 处理 ALTCHA 验证...")
        time.sleep(2)

        # 检查是否已自动通过
        if self.sb.execute_script(_ALTCHA_SOLVED_JS):
            logger.info(f"[{self.masked_user}] ALTCHA 已自动通过")
            return True

        # 获取 ALTCHA iframe 坐标
        coords = None
        try:
            coords = self.sb.execute_script(_ALTCHA_EXPAND_JS)
        except Exception:
            pass

        if coords:
            logger.info(f"[{self.masked_user}] ALTCHA iframe 坐标: ({coords['cx']}, {coords['cy']})")

        # 最多尝试 3 轮
        for attempt in range(3):
            if self.sb.execute_script(_ALTCHA_SOLVED_JS):
                logger.info(f"[{self.masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
                return True

            # 策略 1: xdotool 物理点击（仅 Linux）
            clicked = False
            if coords and HAS_XDOTOOL:
                try:
                    wi = self.sb.execute_script(_WININFO_JS)
                    bar = wi["oh"] - wi["ih"]
                    ax = coords["cx"] + wi["sx"]
                    ay = coords["cy"] + wi["sy"] + bar
                    logger.info(f"[{self.masked_user}] ALTCHA xdotool 点击 ({ax}, {ay})")
                    clicked = _xdotool_click(ax, ay)
                except Exception:
                    pass

            # 策略 1b: ActionChains 偏移点击（Windows/Linux 通用）
            if not clicked and coords:
                _actionchains_click(self.sb, coords, context=f"{self.masked_user} ALTCHA")
                clicked = True

            if clicked:
                for _ in range(6):
                    time.sleep(1)
                    if self.sb.execute_script(_ALTCHA_SOLVED_JS):
                        logger.info(f"[{self.masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
                        return True

            # 策略 2: JS 强制点击
            try:
                self.sb.execute_script(_ALTCHA_FORCE_CLICK_JS)
                logger.info(f"[{self.masked_user}] ALTCHA JS 强制点击")
            except Exception:
                pass

            for _ in range(6):
                time.sleep(1)
                if self.sb.execute_script(_ALTCHA_SOLVED_JS):
                    logger.info(f"[{self.masked_user}] ALTCHA 通过 (第{attempt+1}轮)")
                    return True

            logger.info(f"[{self.masked_user}] ALTCHA 第{attempt+1}轮未通过，重试...")
            # 重新获取坐标
            try:
                new_coords = self.sb.execute_script(_ALTCHA_EXPAND_JS)
                if new_coords:
                    coords = new_coords
            except Exception:
                pass

        logger.error(f"[{self.masked_user}] ALTCHA 3 轮均失败")
        return False

    def _login(self):
        """登录到 Katabump 面板"""
        logger.info(f"开始登录账号: {self.masked_user}")

        # 使用 uc_open_with_reconnect 自动处理 Cloudflare
        logger.info(f"打开登录页面: {BASE_URL}/auth/login")
        try:
            self.sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=5)
        except AttributeError:
            self.sb.open(BASE_URL + "/auth/login")
        time.sleep(5)

        # 等待 Cloudflare 验证通过（最多 30 秒）
        logger.info(f"[{self.masked_user}] 等待 Cloudflare 验证通过...")
        cf_passed = False
        for i in range(30):
            page_src = self.sb.get_page_source() or ""
            if 'input' in page_src.lower() and ('email' in page_src.lower() or 'password' in page_src.lower()):
                cf_passed = True
                logger.info(f"[{self.masked_user}] Cloudflare 验证通过 ({i+1}s)")
                break
            time.sleep(1)

        if not cf_passed:
            logger.warning(f"[{self.masked_user}] Cloudflare 验证可能未通过，继续尝试...")

        # 等待登录表单
        try:
            self.sb.wait_for_element('input#email, input[name="email"]', timeout=15)
        except Exception:
            try:
                self.sb.wait_for_element('input#Email, input[name="Email"]', timeout=5)
            except Exception:
                logger.error(f"[{self.masked_user}] 页面未加载出登录表单")
                try:
                    self.sb.save_screenshot(f"login_fail_{self.masked_user}.png")
                except Exception:
                    pass
                return False

        # 关闭 Cookie 弹窗
        try:
            for btn in self.sb.find_elements("button"):
                if "Accept" in (btn.text or ""):
                    btn.click()
                    time.sleep(0.5)
                    break
        except Exception:
            pass

        # 填写邮箱
        logger.info(f"[{self.masked_user}] 填写用户名/邮箱...")
        js_fill_input(self.sb, 'input#email, input[name="email"]', self.user)
        time.sleep(0.5 + random.random() * 0.5)

        # 填写密码
        logger.info(f"[{self.masked_user}] 填写密码...")
        js_fill_input(self.sb, 'input#password, input[name="password"]', self.password)
        time.sleep(0.5 + random.random() * 0.5)

        # 处理 Turnstile 验证
        if self.sb.execute_script(_EXISTS_TS_JS):
            if not self._handle_turnstile("Login Auth"):
                logger.error(f"[{self.masked_user}] 登录 Turnstile 验证失败")
                return False
        else:
            logger.info(f"[{self.masked_user}] 未检测到 Turnstile")

        # 提交登录（回车键）
        logger.info(f"[{self.masked_user}] 提交登录...")
        self.sb.press_keys('input#password, input[name="password"]', '\n')

        # 等待登录跳转
        logger.info(f"[{self.masked_user}] 等待登录跳转...")
        for _ in range(15):
            time.sleep(1)
            cur_url = self.sb.get_current_url().split('?')[0].lower()
            page_title = self.sb.get_title() or ""
            if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard" in page_title:
                break

        cur_url = self.sb.get_current_url().split('?')[0].lower()
        page_title = self.sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard" in page_title:
            logger.info(f"[{self.masked_user}] 登录成功！")
            return True

        logger.error(f"[{self.masked_user}] 登录失败 (URL: {cur_url})")
        try:
            self.sb.save_screenshot(f"login_fail_{self.masked_user}.png")
        except Exception:
            pass
        return False

    def _goto_server_detail(self):
        """从 Dashboard 进入服务器详情页"""
        logger.info(f"[{self.masked_user}] 进入服务器详情页...")
        time.sleep(5)

        # 检查是否有"还无法续期"的提示
        try:
            alert_el = self.sb.find_element("div.alert", timeout=4)
            alert_text = (alert_el.text or "").strip()
            if alert_text and "can't renew" in alert_text.lower():
                logger.info(f"[{self.masked_user}] 页面提示: {alert_text}")
                return True, "skip", alert_text
        except Exception:
            pass

        # 多策略查找 "See" 链接
        see_link = None
        selectors = [
            'a[href*="/servers/edit?id="]',
            'td a[href*="/servers/edit"]',
            'table a[href*="/servers/edit"]',
            'table td a',
        ]

        for sel in selectors:
            try:
                see_link = self.sb.find_element(sel, timeout=8)
                logger.info(f"[{self.masked_user}] 通过选择器找到链接: {sel}")
                break
            except Exception:
                continue

        # 选择器失败，尝试文本匹配
        if see_link is None:
            logger.info(f"[{self.masked_user}] 选择器未命中，尝试文本匹配...")
            try:
                for a in self.sb.find_elements("a"):
                    if (a.text or "").strip().lower() == "see":
                        see_link = a
                        logger.info(f"[{self.masked_user}] 通过文本 'See' 找到链接")
                        break
            except Exception:
                pass

        if see_link is None:
            logger.error(f"[{self.masked_user}] 未找到 'See' 链接")
            try:
                self.sb.save_screenshot(f"no_see_link_{self.masked_user}.png")
            except Exception:
                pass
            return False, None, None

        # 点击 See 链接
        logger.info(f"[{self.masked_user}] 点击 'See' 进入服务器详情页...")
        try:
            see_link.click()
        except Exception:
            self.sb.execute_script("arguments[0].click();", see_link)
        time.sleep(5)
        return True, None, None

    def _check_expiry(self):
        """检查是否已到续期日期"""
        logger.info(f"[{self.masked_user}] 检查续期日期...")
        try:
            expiry_element = self.sb.find_element(
                "//div[contains(text(), 'Expiry')]/following-sibling::div",
                timeout=10, by="xpath"
            )
            expiry_text = expiry_element.text.strip()
            logger.info(f"[{self.masked_user}] 到期时间: {expiry_text}")

            tz_hkt = timezone(timedelta(hours=8))
            today = datetime.now(tz_hkt).date()

            expiry_date = None
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y年%m月%d日"]:
                try:
                    expiry_date = datetime.strptime(expiry_text, fmt).date()
                    break
                except ValueError:
                    continue

            if expiry_date is None:
                logger.warning(f"[{self.masked_user}] 无法解析日期 '{expiry_text}'，继续执行续期")
                return True

            if expiry_date != today:
                days_diff = (expiry_date - today).days
                if days_diff > 0:
                    notice = f"未到续期时间，到期日: {expiry_text}，剩余 {days_diff} 天"
                else:
                    notice = f"日期异常，到期日: {expiry_text} (已过 {abs(days_diff)} 天)"
                logger.info(f"[{self.masked_user}] {notice}")
                return False, notice

            logger.info(f"[{self.masked_user}] 今天是续期日，继续执行")
            return True
        except Exception as e:
            logger.warning(f"[{self.masked_user}] 检查续期日期异常: {e}，继续执行")
            return True

    def _do_renew(self):
        """执行续期操作"""
        logger.info(f"[{self.masked_user}] 准备续期流程...")

        # 读取当前到期时间
        initial_expiry = ""
        try:
            expiry_el = self.sb.find_element(
                "//div[contains(text(), 'Expiry')]/following-sibling::div",
                timeout=10, by="xpath"
            )
            initial_expiry = expiry_el.text.strip()
            logger.info(f"[{self.masked_user}] 当前到期时间: {initial_expiry}")
        except Exception:
            logger.warning(f"[{self.masked_user}] 无法读取初始时间")

        # 查找并点击 Renew 按钮
        logger.info(f"[{self.masked_user}] 查找 Renew 按钮...")
        renew_btn = None
        try:
            renew_btn = self.sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
        except Exception:
            try:
                renew_btn = self.sb.find_element('button.btn.btn-outline-primary', timeout=5)
            except Exception:
                logger.error(f"[{self.masked_user}] 未找到 Renew 按钮")
                return False, "未找到 Renew 按钮"

        # 滚动到按钮并点击
        self.sb.execute_script("arguments[0].scrollIntoView({block: 'center'});", renew_btn)
        time.sleep(0.8)
        try:
            renew_btn.click()
        except Exception:
            self.sb.execute_script("arguments[0].click();", renew_btn)
        logger.info(f"[{self.masked_user}] 已点击 Renew 按钮")
        time.sleep(3)

        # 确认模态框已弹出
        try:
            self.sb.find_element("div.modal.show", timeout=5)
            logger.info(f"[{self.masked_user}] Renew 模态框已弹出")
        except Exception:
            logger.warning(f"[{self.masked_user}] 模态框未弹出，尝试继续...")

        # 处理 ALTCHA 验证
        altcha_ok = self._handle_altcha()
        if not altcha_ok:
            logger.warning(f"[{self.masked_user}] ALTCHA 验证未完全通过，仍尝试提交...")

        # 点击最终 Renew 提交按钮
        logger.info(f"[{self.masked_user}] 点击提交 Renew...")
        try:
            submit_btn = self.sb.find_element(
                'div.modal.show button.btn-primary, div.modal.show button[type="submit"]',
                timeout=5
            )
            submit_btn.click()
        except Exception:
            self.sb.execute_script("""
                (function(){
                    var m = document.querySelector('div.modal.show');
                    if (!m) return;
                    var bs = m.querySelectorAll('button');
                    for (var i = 0; i < bs.length; i++)
                        if (/renew/i.test(bs[i].textContent)) bs[i].click();
                })()
            """)
        logger.info(f"[{self.masked_user}] 等待续期结果...")
        time.sleep(7 + random.random() * 3)

        # 核验结果
        try:
            alerts = self.sb.find_elements("div.alert-danger, div.alert")
            if alerts:
                alert_text = (alerts[0].text or "").strip().replace('x', '')
                if alert_text:
                    logger.info(f"[{self.masked_user}] 页面提示: {alert_text}")
                    low = alert_text.lower()
                    if any(kw in low for kw in ["renewed", "success", "extended"]):
                        return True, f"续期成功: {alert_text}"
                    elif "can't renew" in low or "unable" in low:
                        return False, f"未到续期时间: {alert_text}"
                    else:
                        return False, alert_text

            # 检查到期时间是否已更新
            try:
                final_expiry_el = self.sb.find_element(
                    "//div[contains(text(), 'Expiry')]/following-sibling::div",
                    timeout=5, by="xpath"
                )
                final_expiry = final_expiry_el.text.strip()
                logger.info(f"[{self.masked_user}] 续期后到期时间: {final_expiry}")
                if final_expiry != initial_expiry and len(final_expiry) > 0:
                    return True, f"续期成功: {final_expiry}"
                else:
                    return False, f"到期时间未更新 ({initial_expiry})"
            except Exception:
                return False, "无法获取续期结果"
        except Exception as e:
            return False, f"验证结果出错: {e}"

    def run(self):
        """核心运行逻辑，带重试机制"""
        max_retries = 3
        last_error = ""

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"[{self.masked_user}] 正在进行第 {attempt+1} 次尝试...")
                    try:
                        self.sb.driver.refresh()
                    except Exception:
                        pass
                    time.sleep(5 + random.random() * 3)

                # 登录
                if not self._login():
                    last_error = "登录失败"
                    continue

                # 进入服务器详情页
                detail_ok, skip_flag, skip_msg = self._goto_server_detail()
                if not detail_ok:
                    last_error = "无法进入服务器详情页"
                    continue

                if skip_flag == "skip":
                    self.screenshot_path = None
                    return True, skip_msg

                # 检查续期日期
                expiry_check = self._check_expiry()
                if isinstance(expiry_check, tuple):
                    self.screenshot_path = None
                    return True, expiry_check[1]

                # 执行续期
                success, message = self._do_renew()
                if success:
                    return True, message
                else:
                    last_error = message
                    if "未到续期时间" in message:
                        return True, message
                    break

            except Exception as e:
                last_error = f"异常: {str(e)[:100]}"
                logger.error(f"[{self.masked_user}] 第 {attempt+1} 次执行出错: {e}")

            if attempt < max_retries - 1:
                time.sleep(5 + random.random() * 5)

        # 最终失败
        screenshot_file = f"error-{self.user.split('@')[0]}.png"
        try:
            self.sb.save_screenshot(screenshot_file)
            self.screenshot_path = screenshot_file
        except Exception:
            pass
        return False, f"历经 {max_retries} 次尝试仍失败: {last_error}"


# ===================== 主逻辑管理 =====================

class MultiManager:
    def __init__(self, sb):
        self.sb = sb
        raw_accs = re.split(r'[,;]', ACCOUNTS_ENV)
        self.accounts = []
        for a in raw_accs:
            if ':' in a:
                u, p = a.split(':', 1)
                self.accounts.append({'user': u.strip(), 'pass': p.strip()})

    def run_all(self):
        total = len(self.accounts)
        logger.info(f"发现 {total} 个账号需要续期")
        results = []
        last_screenshot = None
        success_count = 0

        for i, acc in enumerate(self.accounts):
            logger.info(f"\n--- 处理第 {i+1}/{total} 个账号 ---")
            bot = KatabumpAutoRenew(acc['user'], acc['pass'])
            bot.sb = self.sb
            success, msg = bot.run()
            results.append({'message': msg, 'success': success})
            if success:
                success_count += 1
            if bot.screenshot_path:
                last_screenshot = bot.screenshot_path

            if i < total - 1:
                wait_time = PAUSE_BETWEEN_ACCOUNTS_MS + random.random() * 5000
                logger.info(f"账号间歇期：等待 {round(wait_time/1000)} 秒...")
                time.sleep(wait_time / 1000)

        # 汇总通知
        masked_msgs = []
        for r in results:
            msg = r['message']
            # 邮箱已在消息中由 mask_email 处理
            masked_msgs.append(msg)

        summary = f"续期汇总: {success_count}/{total} 个账号成功\n\n"
        summary += "\n\n".join(masked_msgs)
        send_telegram(summary, last_screenshot)

        # 清理截图
        if last_screenshot and os.path.exists(last_screenshot):
            import glob
            for f in glob.glob("error-*.png"):
                try:
                    os.remove(f)
                except Exception:
                    pass

        logger.info(f"\n所有账号处理完成！{success_count}/{total} 成功")
        return success_count, total


# ===================== 入口 =====================

def main():
    if not ACCOUNTS_ENV:
        logger.error("未配置账号（ACCOUNTS 环境变量）")
        print("请设置环境变量 ACCOUNTS，格式: user1:pass1,user2:pass2")
        exit(1)

    # 配置 seleniumbase 参数
    # Windows 上 UC 模式可能因 chromedriver 下载失败而不可用
    # 所以先尝试 UC，失败后回退到普通模式
    use_uc = os.environ.get('USE_UC', 'true').lower() == 'true' and not IS_WINDOWS

    sb_kwargs = {
        "uc": use_uc,
        "headless": HEADLESS,
        "chromium_arg": "--no-first-run --disable-extensions --disable-gpu --no-sandbox --disable-dev-shm-usage",
    }

    if PROXY_SERVER:
        sb_kwargs["proxy"] = PROXY_SERVER
        logger.info(f"使用代理: {PROXY_SERVER}")

    logger.info("Katabump 自动续期脚本启动")
    logger.info(f"无头模式: {HEADLESS}")
    logger.info(f"UC 模式: {use_uc}")
    logger.info(f"平台: {'Windows' if IS_WINDOWS else 'Linux'}")

    sb = None
    try:
        sb = SB(**sb_kwargs)
        sb.__enter__()
        # Windows Chrome 启动修复：确保窗口大小正确
        sb.driver.set_window_size(1280, 800)

        # 显示出口 IP
        try:
            sb.open("https://api.ip.sb/ip")
            ip_text = sb.get_text("body").strip()
            logger.info(f"当前出口 IP: {ip_text}")
        except Exception:
            pass

        manager = MultiManager(sb)
        manager.run_all()
    except Exception as e:
        error_str = str(e)
        logger.warning(f"启动失败: {error_str[:200]}")

        # 清理
        if sb:
            try:
                sb.__exit__(None, None, None)
            except Exception:
                pass
            sb = None

        # 回退到普通模式
        if use_uc or "Connection aborted" in error_str or "10054" in error_str:
            logger.info("回退到普通 Selenium 模式...")
            try:
                sb_kwargs["uc"] = False
                sb = SB(**sb_kwargs)
                sb.__enter__()
                sb.driver.set_window_size(1280, 800)

                try:
                    sb.open("https://api.ip.sb/ip")
                    ip_text = sb.get_text("body").strip()
                    logger.info(f"当前出口 IP: {ip_text}")
                except Exception:
                    pass

                manager = MultiManager(sb)
                manager.run_all()
            except Exception as e2:
                logger.error(f"普通模式也失败: {e2}")
                import traceback
                traceback.print_exc()
                if sb:
                    try:
                        sb.__exit__(None, None, None)
                    except Exception:
                        pass
                exit(1)
        else:
            logger.error(f"脚本运行出错: {e}")
            import traceback
            traceback.print_exc()
            if sb:
                try:
                    sb.__exit__(None, None, None)
                except Exception:
                    pass
            exit(1)
    finally:
        if sb:
            try:
                sb.__exit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    main()