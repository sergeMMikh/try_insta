from pathlib import Path
from typing import Optional

from dotenv import dotenv_values
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def create_chrome_driver(
    user_agent: Optional[str] = None,
    headless: bool = False,
    user_data_dir: Optional[str] = None,
    profile_directory: Optional[str] = None,
) -> WebDriver:
    """Create a Chrome WebDriver with optional fixed User-Agent."""
    options = ChromeOptions()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1366,900")

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    if profile_directory:
        options.add_argument(f"--profile-directory={profile_directory}")

    return webdriver.Chrome(options=options)


def _load_env_credentials() -> tuple[str, str, str, str, str]:
    """Load Instagram and Facebook credentials from parsing/.env."""
    env_path = Path(__file__).with_name(".env")
    env_values = dotenv_values(env_path)

    insta_username = env_values.get("INSTA_USERNAME") or env_values.get("IG_USERNAME")
    insta_password = env_values.get("INSTA_PASSWORD") or env_values.get("IG_PASSWORD")
    fb_username = env_values.get("FB_USERNAME")
    fb_password = env_values.get("FB_PASSWORD")

    return (
        env_path.as_posix(),
        insta_username or "",
        insta_password or "",
        fb_username or "",
        fb_password or "",
    )


def _safe_click(driver: WebDriver, element) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            element,
        )
    except Exception:
        pass

    try:
        element.click()
        return True
    except Exception:
        pass

    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
        return True
    except Exception:
        pass

    try:
        element.send_keys(Keys.ENTER)
        return True
    except Exception:
        pass

    try:
        element.send_keys(Keys.SPACE)
        return True
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        return False


def _click_if_present(
    driver: WebDriver, selectors: list[tuple[str, str]], timeout: int = 2
) -> bool:
    for by, selector in selectors:
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
            if _safe_click(driver, element):
                return True
        except TimeoutException:
            continue
    return False


def _switch_to_new_window_if_opened(driver: WebDriver, timeout: int = 5) -> None:
    """If OAuth opens a new window/tab, switch into it."""
    original = driver.current_window_handle
    try:
        WebDriverWait(driver, timeout).until(lambda d: len(d.window_handles) > 1)
    except TimeoutException:
        return

    for handle in driver.window_handles:
        if handle != original:
            driver.switch_to.window(handle)
            return


def _find_and_click_facebook_cta_in_context(driver: WebDriver, timeout: int) -> bool:
    return _click_if_present(
        driver,
        [
            (By.XPATH, "//button[contains(., 'Facebook')]"),
            (By.XPATH, "//a[contains(., 'Facebook')]"),
            (By.XPATH, "//*[@role='button' and contains(., 'Facebook')]"),
            (
                By.XPATH,
                "//*[contains(., 'Facebook')]/ancestor::*[self::button or self::a or @role='button'][1]",
            ),
        ],
        timeout=timeout,
    )


def _click_facebook_cta_across_frames(driver: WebDriver, timeout: int = 8) -> bool:
    """Try CTA click in top document and in any iframe."""
    if _find_and_click_facebook_cta_in_context(driver, timeout):
        return True

    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for idx, frame in enumerate(frames):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            if _find_and_click_facebook_cta_in_context(driver, min(timeout, 3)):
                driver.switch_to.default_content()
                return True
        except Exception:
            continue
    driver.switch_to.default_content()
    return False


def _keyboard_activate_facebook_cta(driver: WebDriver, max_tabs: int = 25) -> bool:
    """Fallback: navigate focus with Tab until active element mentions Facebook, then press Enter."""
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    try:
        body = driver.find_element(By.TAG_NAME, "body")
        _safe_click(driver, body)
    except Exception:
        pass

    for _ in range(max_tabs):
        try:
            driver.switch_to.active_element.send_keys(Keys.TAB)
        except Exception:
            return False

        try:
            active = driver.switch_to.active_element
            text = " ".join(
                [
                    (active.text or "").strip(),
                    (active.get_attribute("aria-label") or "").strip(),
                    (active.get_attribute("title") or "").strip(),
                ]
            ).strip()
            if "facebook" in text.lower():
                try:
                    active.send_keys(Keys.ENTER)
                except Exception:
                    _safe_click(driver, active)
                return True
        except Exception:
            continue

    return False


def _log_facebook_cta_diagnostics(driver: WebDriver) -> None:
    """Print minimal diagnostics to understand why CTA click does not redirect."""
    try:
        info = driver.execute_script(
            """
            const isCtaText = (txt) => /facebook/i.test(txt) && /(log\\s*in|continue|войти)/i.test(txt);
            const result = {
              url: location.href,
              anchors: 0,
              buttons: 0,
              oauthHref: null,
              texts: [],
              ctaCandidates: [],
            };
            const nodes = [...document.querySelectorAll('a,button,[role="button"]')];
            for (const n of nodes) {
              const txt = (n.innerText || n.textContent || '').trim();
              const href = n.href || (n.getAttribute && n.getAttribute('href')) || '';
              if (/facebook/i.test(txt) || /facebook/i.test(href)) {
                result.texts.push(txt.slice(0, 120));
                if (n.tagName === 'A') result.anchors += 1;
                if (n.tagName === 'BUTTON') result.buttons += 1;
                if (!result.oauthHref && href) result.oauthHref = href;
                if (isCtaText(txt)) {
                  result.ctaCandidates.push({ text: txt.slice(0, 120), href });
                }
              }
            }
            result.iframes = document.querySelectorAll('iframe').length;
            return result;
            """
        )
        return
    except Exception:
        return


def _click_instagram_facebook_login(driver: WebDriver, timeout: int = 8) -> bool:
    """Click Instagram's 'Continue with Facebook' CTA with robust fallbacks."""
    if _click_facebook_cta_across_frames(driver, timeout=timeout):
        return True

    clicked = driver.execute_script(
        """
        const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
        const nodes = [...document.querySelectorAll('button,a,[role="button"],span,div')];
        for (const node of nodes) {
          const text = (node.innerText || node.textContent || '').trim();
          if (!text || !/facebook/i.test(text)) continue;
          let cur = node;
          while (cur && cur !== document.body) {
            const tag = (cur.tagName || '').toLowerCase();
            const role = cur.getAttribute ? (cur.getAttribute('role') || '') : '';
            if ((tag === 'button' || tag === 'a' || role === 'button') && visible(cur)) {
              cur.click();
              return true;
            }
            cur = cur.parentElement;
          }
        }
        return false;
        """
    )
    if bool(clicked):
        return True

    if _keyboard_activate_facebook_cta(driver):
        return True

    href = _extract_facebook_oauth_href(driver)
    if href:
        driver.get(str(href))
        return True

    return False


def _extract_facebook_oauth_href(driver: WebDriver) -> Optional[str]:
    href = driver.execute_script(
        """
        const isBadHref = (href) =>
          !href ||
          href === '#' ||
          /^javascript:/i.test(href) ||
          /developers\\.facebook\\.com/i.test(href) ||
          /facebook\\.com\\/help\\//i.test(href);
        const ctaText = (a) => ((a.innerText || a.textContent || '') + ' ' + (a.getAttribute('aria-label') || '')).trim();
        const isLoginCtaText = (text) => /facebook/i.test(text) && /(log\\s*in|continue|войти)/i.test(text);
        const links = [...document.querySelectorAll('a[href]')];

        // Prefer the exact visible "Continue/Login with Facebook" CTA.
        for (const a of links) {
          const text = ctaText(a);
          const href = a.href || '';
          if (isLoginCtaText(text) && !isBadHref(href)) return href;
        }

        // Secondary fallback: any Facebook/OAuth-looking href, but skip docs links.
        for (const a of links) {
          const text = ctaText(a);
          const href = a.href || '';
          if (isBadHref(href)) continue;
          if (/facebook/i.test(text) || /facebook\\.com/i.test(href) || /oauth/i.test(href)) return href;
        }
        return null;
        """
    )
    return str(href) if href else None


def _login_instagram_direct(
    driver: WebDriver,
    env_path: str,
    insta_username: str,
    insta_password: str,
    timeout: int,
) -> None:
    if not insta_username or not insta_password:
        raise ValueError(
            f"INSTA_USERNAME/INSTA_PASSWORD not found in {env_path}. "
            "Set them in parsing/.env before login."
        )

    wait = WebDriverWait(driver, timeout)

    username_selectors = [
        (By.NAME, "username"),
        (By.CSS_SELECTOR, "input[name='username']"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
    ]
    password_selectors = [
        (By.NAME, "password"),
        (By.CSS_SELECTOR, "input[name='password']"),
        (By.CSS_SELECTOR, "input[type='password']"),
    ]

    _type_first_visible(driver, username_selectors, insta_username, timeout)
    _type_first_visible(driver, password_selectors, insta_password, timeout)
    username_len, password_len = _debug_input_lengths(driver)

    if username_len == 0 or password_len == 0:
        for css, value in (
            ("input[name='username']", insta_username),
            ("input[name='password']", insta_password),
        ):
            for element in driver.find_elements(By.CSS_SELECTOR, css):
                if element.is_displayed() and element.is_enabled():
                    _set_react_input_value(driver, element, value)
                    break
        username_len, password_len = _debug_input_lengths(driver)

    _submit_instagram_login(driver)

    try:
        wait.until(
            lambda d: "/accounts/login" not in d.current_url
            or bool(d.find_elements(By.XPATH, "//nav"))
        )
    except TimeoutException:
        pass

    _dismiss_post_login_dialogs(driver)
    print(f"Using credentials from {env_path} for Instagram login.")


def _set_react_input_value(driver: WebDriver, element, value: str) -> None:
    """Set value and emit events for React-controlled inputs."""
    driver.execute_script(
        """
        const el = arguments[0];
        const val = arguments[1];
        const proto = Object.getPrototypeOf(el);
        const desc = Object.getOwnPropertyDescriptor(proto, 'value')
          || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
        const prev = el.value;
        if (desc && desc.set) desc.set.call(el, val);
        else el.value = val;
        if (el._valueTracker && el._valueTracker.setValue) {
          el._valueTracker.setValue(prev);
        }
        el.focus();
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        """,
        element,
        value,
    )


def _type_first_visible(
    driver: WebDriver, selectors: list[tuple[str, str]], value: str, timeout: int
) -> None:
    last_error: Optional[Exception] = None
    for by, selector in selectors:
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: any(
                    el.is_displayed() and el.is_enabled()
                    for el in d.find_elements(by, selector)
                )
            )
            for element in driver.find_elements(by, selector):
                if not (element.is_displayed() and element.is_enabled()):
                    continue

                try:
                    _safe_click(driver, element)
                except Exception:
                    pass

                try:
                    element.send_keys(Keys.CONTROL, "a")
                    element.send_keys(Keys.DELETE)
                except Exception:
                    try:
                        element.clear()
                    except Exception:
                        pass

                element.send_keys(value)
                if (element.get_attribute("value") or "") == "":
                    _set_react_input_value(driver, element, value)
                return
        except (TimeoutException, StaleElementReferenceException) as exc:
            last_error = exc
            continue

    raise TimeoutException(f"Could not find visible input for selectors: {selectors}") from last_error


def _debug_input_lengths(driver: WebDriver) -> tuple[int, int]:
    username_len = driver.execute_script(
        """
        const els = [...document.querySelectorAll("input[name='username']")];
        const el = els.find(e => e.offsetParent !== null) || els[0];
        return el ? (el.value || '').length : -1;
        """
    )
    password_len = driver.execute_script(
        """
        const els = [...document.querySelectorAll("input[name='password']")];
        const el = els.find(e => e.offsetParent !== null) || els[0];
        return el ? (el.value || '').length : -1;
        """
    )
    return int(username_len), int(password_len)


def _submit_instagram_login(driver: WebDriver) -> None:
    submit_selectors = [
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.XPATH, "//button[contains(., 'Log in')]"),
        (By.XPATH, "//*[@role='button' and contains(., 'Log in')]"),
    ]
    if _click_if_present(driver, submit_selectors, timeout=3):
        return

    try:
        driver.switch_to.active_element.send_keys(Keys.ENTER)
    except Exception:
        pass


def _dismiss_post_login_dialogs(driver: WebDriver) -> None:
    dismiss_selectors = [
        (By.XPATH, "//button[contains(., 'Not now')]"),
        (By.XPATH, "//button[contains(., 'Later')]"),
        (By.XPATH, "//*[@role='button' and contains(., 'Not now')]"),
    ]
    for _ in range(2):
        _click_if_present(driver, dismiss_selectors, timeout=3)


def _click_facebook_authorize_continue(driver: WebDriver, timeout: int = 5) -> bool:
    """Handle Facebook/Meta consent screens like 'Continue as ...' before login-form logic."""
    selectors = [
        (By.XPATH, "//button[contains(., 'Continue as')]"),
        (By.XPATH, "//button[contains(., 'Continue')]"),
        (By.XPATH, "//button[contains(., 'Allow')]"),
        (By.XPATH, "//button[contains(., 'Authorize')]"),
        (By.XPATH, "//button[contains(., 'Продолжить как')]"),
        (By.XPATH, "//button[contains(., 'Продолжить')]"),
        (By.XPATH, "//button[contains(., 'Разрешить')]"),
        (By.XPATH, "//div[@role='button' and contains(., 'Continue as')]"),
        (By.XPATH, "//div[@role='button' and contains(., 'Continue')]"),
        (By.XPATH, "//div[@role='button' and contains(., 'Allow')]"),
        (By.XPATH, "//div[@role='button' and contains(., 'Продолжить как')]"),
        (By.XPATH, "//div[@role='button' and contains(., 'Продолжить')]"),
        (By.XPATH, "//a[contains(., 'Continue as')]"),
        (By.XPATH, "//a[contains(., 'Продолжить как')]"),
    ]
    clicked = _click_if_present(driver, selectors, timeout=timeout)
    return clicked


def _login_facebook_form(driver: WebDriver, username: str, password: str, timeout: int) -> None:
    wait = WebDriverWait(driver, timeout)

    # OAuth can open a consent screen ("Continue as ...") instead of login fields.
    if _click_facebook_authorize_continue(driver, timeout=4):
        return

    # If already logged in on Facebook, OAuth can show a continue/authorize action
    # instead of email/password fields. Do not click generic submit on the login form.
    has_login_fields = any(
        el.is_displayed() and el.is_enabled()
        for el in driver.find_elements(By.NAME, "email")
    ) and any(
        el.is_displayed() and el.is_enabled()
        for el in driver.find_elements(By.NAME, "pass")
    )

    if not has_login_fields and "facebook.com" in driver.current_url.lower():
        if _click_facebook_authorize_continue(driver, timeout=4):
            return

    _type_first_visible(
        driver,
        [
            (By.NAME, "email"),
            (By.CSS_SELECTOR, "input[name='email']"),
            (By.CSS_SELECTOR, "input[autocomplete='username']"),
            (By.CSS_SELECTOR, "input[type='text']"),
            (By.CSS_SELECTOR, "input[type='email']"),
        ],
        username,
        timeout,
    )
    _type_first_visible(
        driver,
        [
            (By.NAME, "pass"),
            (By.CSS_SELECTOR, "input[name='pass']"),
            (By.CSS_SELECTOR, "input[type='password']"),
            (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
        ],
        password,
        timeout,
    )

    email_len = int(
        driver.execute_script(
            """
            const els=[...document.querySelectorAll("input[name='email']")];
            const el=els.find(e => e.offsetParent !== null) || els[0];
            return el ? (el.value || '').length : -1;
            """
        )
    )
    pass_len = int(
        driver.execute_script(
            """
            const els=[...document.querySelectorAll("input[name='pass']")];
            const el=els.find(e => e.offsetParent !== null) || els[0];
            return el ? (el.value || '').length : -1;
            """
        )
    )
    submit_selectors = [
        (By.CSS_SELECTOR, "button[name='login']"),
        (By.CSS_SELECTOR, "input[name='login']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//*[@aria-label='Log in']"),
        (By.XPATH, "//button[contains(., 'Log in')]"),
        (By.XPATH, "//button[contains(., 'Войти')]"),
    ]
    if _click_if_present(driver, submit_selectors, timeout=3):
        return

    try:
        driver.find_element(By.NAME, "pass").send_keys(Keys.ENTER)
    except Exception:
        driver.switch_to.active_element.send_keys(Keys.ENTER)


def login_instagram(
    driver: WebDriver, timeout: int = 20, use_facebook: bool = False
) -> None:
    """
    Log in to Instagram using credentials from parsing/.env.

    By default uses direct Instagram login (INSTA_USERNAME / INSTA_PASSWORD).
    Set use_facebook=True to click Continue with Facebook and use FB_USERNAME / FB_PASSWORD.
    """
    env_path, insta_username, insta_password, fb_username, fb_password = _load_env_credentials()

    driver.get("https://www.instagram.com/accounts/login/")
    wait = WebDriverWait(driver, timeout)

    # Optional cookie banner.
    _click_if_present(
        driver,
        [
            (By.XPATH, "//button[contains(., 'Allow all cookies')]"),
            (By.XPATH, "//button[contains(., 'Only allow essential cookies')]"),
            (By.XPATH, "//button[contains(., 'cookie')]"),
        ],
        timeout=3,
    )

    if use_facebook:
        if not fb_username or not fb_password:
            raise ValueError(
                f"FB_USERNAME/FB_PASSWORD not found in {env_path}. "
                "Set them in parsing/.env before Instagram login via Facebook."
            )

        before_url = driver.current_url
        before_handles = list(driver.window_handles)
        direct_oauth_href = _extract_facebook_oauth_href(driver)
        if direct_oauth_href and direct_oauth_href != driver.current_url:
            driver.get(direct_oauth_href)
            clicked = True
        else:
            clicked = _click_instagram_facebook_login(driver, timeout=8)
        if not clicked:
            clicked = _click_if_present(
                driver,
                [
                    (
                        By.XPATH,
                        "//a[contains(@href,'facebook.com') or contains(@href,'oauth')]",
                    )
                ],
                timeout=5,
            )
        if not clicked:
            raise TimeoutException("Instagram Facebook login button was not found/clickable.")

        _switch_to_new_window_if_opened(driver)
        try:
            WebDriverWait(driver, min(timeout, 5)).until(
                lambda d: "facebook.com" in d.current_url.lower()
                or len(d.window_handles) > len(before_handles)
                or d.current_url != before_url
            )
        except TimeoutException:
            href = _extract_facebook_oauth_href(driver)
            if href and href != driver.current_url:
                driver.get(href)
                _switch_to_new_window_if_opened(driver)
                wait.until(
                    lambda d: "facebook.com" in d.current_url.lower()
                    or d.current_url != before_url
                )
            else:
                _login_instagram_direct(
                    driver, env_path, insta_username, insta_password, timeout
                )
                return

        _switch_to_new_window_if_opened(driver)
        if "facebook.com" not in driver.current_url.lower():
            driver.get("https://www.instagram.com/accounts/login/")
            _login_instagram_direct(driver, env_path, insta_username, insta_password, timeout)
            return
        _login_facebook_form(driver, fb_username, fb_password, timeout)
        print(f"Using credentials from {env_path} for Instagram login via Facebook.")
        return

    _login_instagram_direct(driver, env_path, insta_username, insta_password, timeout)


if __name__ == "__main__":
    env_path = Path(__file__).with_name(".env")
    env_values = dotenv_values(env_path)

    # Default: use a dedicated persistent Selenium profile in the project.
    # You can override with CHROME_USER_DATA_DIR / CHROME_PROFILE_DIRECTORY in parsing/.env
    # to reuse an existing Chrome profile.
    chrome_user_data_dir = (
        env_values.get("CHROME_USER_DATA_DIR")
        or str(Path(__file__).with_name(".chrome-profile"))
    )
    chrome_profile_directory = env_values.get("CHROME_PROFILE_DIRECTORY")

    driver = create_chrome_driver(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        user_data_dir=chrome_user_data_dir,
        profile_directory=chrome_profile_directory,
    )
    try:
        print(f"Chrome profile dir: {chrome_user_data_dir}")
        if chrome_profile_directory:
            print(f"Chrome profile name: {chrome_profile_directory}")

        driver.get("https://www.instagram.com/")
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")

        if "/accounts/login" in driver.current_url:
            print(
                "Instagram session not found in this Chrome profile. "
                "Log in manually once (you can use Facebook button), then press Enter."
            )
            input("Press Enter after manual login is complete...")
            print(f"Current URL after manual login: {driver.current_url}")
        else:
            print(f"Instagram session reused. Current URL: {driver.current_url}")

        input("Press Enter to close browser...")
    finally:
        driver.quit()
