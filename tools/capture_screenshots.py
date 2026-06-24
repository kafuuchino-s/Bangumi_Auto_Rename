import time
from pathlib import Path

from patchright.sync_api import sync_playwright

SCREENSHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)

BASE_URL = "http://127.0.0.1:5999"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 900})

    # 任务页（主页）
    page.goto(BASE_URL, wait_until="load")
    time.sleep(3)
    page.screenshot(path=str(SCREENSHOTS / "tasks.png"), full_page=False)
    print("tasks.png saved")

    # 字幕页
    page.goto(f"{BASE_URL}/subtitles", wait_until="load")
    time.sleep(3)
    page.screenshot(path=str(SCREENSHOTS / "subtitles.png"), full_page=False)
    print("subtitles.png saved")

    # 配置页
    page.goto(f"{BASE_URL}/settings/general", wait_until="load")
    time.sleep(3)
    page.screenshot(path=str(SCREENSHOTS / "settings.png"), full_page=False)
    print("settings.png saved")

    browser.close()
