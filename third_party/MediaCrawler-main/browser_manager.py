import os
import socket
import subprocess
import time
import requests
from playwright.async_api import async_playwright


def get_free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_cdp(port, timeout=15):
    url = f"http://127.0.0.1:{port}/json/version"
    start = time.time()

    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)

    return False


class BrowserManager:
    def __init__(self, chrome_path, user_data_dir="./chrome_profile"):
        self.chrome_path = chrome_path
        self.user_data_dir = os.path.abspath(user_data_dir)
        self.port = get_free_port()
        self.process = None

    def start(self):
        if self.process is not None:
            return

        if not os.path.exists(self.chrome_path):
            raise FileNotFoundError(f"浏览器路径不存在: {self.chrome_path}")

        os.makedirs(self.user_data_dir, exist_ok=True)

        cmd = [
            self.chrome_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
        ]

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        ok = wait_for_cdp(self.port, timeout=20)
        if not ok:
            raise RuntimeError(
                f"浏览器启动了，但 CDP 端口不可用: 127.0.0.1:{self.port}\n"
                f"请检查浏览器路径、是否已有同 user-data-dir 的浏览器进程、或是否被杀毒软件拦截。"
            )

    def get_cdp_url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        if self.process:
            self.process.terminate()
            self.process = None


class BrowserClient:
    def __init__(self, cdp_url):
        self.cdp_url = cdp_url
        self.playwright = None
        self.browser = None
        self.context = None

    async def connect(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)

        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = await self.browser.new_context()

    async def new_page(self):
        return await self.context.new_page()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()