from pathlib import Path
from typing import Optional

from dotenv import dotenv_values
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def create_chrome_driver(
    user_agent: Optional[str] = None, headless: bool = False
) -> WebDriver:
    """Create a Chrome WebDriver with optional fixed User-Agent."""
    options = ChromeOptions()

    if headless:
        options.add_argument("--headless=new")

    # Force a desktop-like viewport; --start-maximized is not reliable in WebDriver.
    options.add_argument("--window-size=1366,900")

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    return webdriver.Chrome(options=options)


def _click_login_submit(driver: WebDriver, timeout: int) -> None:
    """Try multiple submit strategies across desktop/mobile Facebook layouts."""
    login_selectors = [
        (By.CSS_SELECTOR, "button[name='login']"),
        (By.CSS_SELECTOR, "input[name='login']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//*[@aria-label='Log in']"),
    ]

    for by, selector in login_selectors:
        try:
            button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, selector))
            )
            button.click()
            return
        except TimeoutException:
            continue

    # Fallback: try candidates inside the same form as the password field.
    for _ in range(2):
        try:
            password_input = driver.find_element(By.NAME, "pass")
            form = password_input.find_element(By.XPATH, "./ancestor::form[1]")
            for selector in ("button", "input[type='submit']"):
                for candidate in form.find_elements(By.CSS_SELECTOR, selector):
                    if candidate.is_displayed() and candidate.is_enabled():
                        candidate.click()
                        return

            for candidate in form.find_elements(By.CSS_SELECTOR, "[role='button']"):
                text = (candidate.text or "").strip()
                value = (candidate.get_attribute("value") or "").strip()
                label = (candidate.get_attribute("aria-label") or "").strip()
                # Skip icon controls (for example password visibility toggle).
                if not any((text, value, label)):
                    continue
                if candidate.is_displayed() and candidate.is_enabled():
                    candidate.click()
                    return
            break
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    # Last resort: re-find password input and submit with Enter.
    try:
        driver.find_element(By.NAME, "pass").send_keys(Keys.ENTER)
    except (NoSuchElementException, StaleElementReferenceException):
        pass


def login_facebook(driver: WebDriver, timeout: int = 20) -> None:
    """Log in to Facebook using credentials from parsing/.env."""
    env_path = Path(__file__).with_name(".env")
    env_values = dotenv_values(env_path)

    # Read directly from parsing/.env to avoid collision with Windows %USERNAME%.
    username = env_values.get("FB_USERNAME") or env_values.get("USERNAME")
    password = env_values.get("FB_PASSWORD") or env_values.get("PASSWORD")

    if not username or not password:
        raise ValueError(
            f"FB_USERNAME/FB_PASSWORD (or USERNAME/PASSWORD) not found in {env_path}. "
            "Set them in parsing/.env before login."
        )

    print(f"Using credentials from {env_path} for Facebook login.")

    driver.get("https://www.facebook.com/")
    wait = WebDriverWait(driver, timeout)

    email_input = wait.until(EC.presence_of_element_located((By.NAME, "email")))
    password_input = wait.until(EC.presence_of_element_located((By.NAME, "pass")))

    email_input.clear()
    email_input.send_keys(username)
    password_input.clear()
    password_input.send_keys(password)

    _click_login_submit(driver, timeout)


if __name__ == "__main__":
    # Example: use a stable UA instead of randomizing on every run.
    driver = create_chrome_driver(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    )
    try:
        login_facebook(driver)
        input("Press Enter to close browser...")
    finally:
        driver.quit()
