"""Automation helpers for fetching Morningstar CSV exports via Selenium."""

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Tuple

from dotenv import find_dotenv, load_dotenv

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import Chrome
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ms_screener.src.logging_setup import console

load_dotenv(find_dotenv())

LOGIN_URL = "https://ezproxy.spl.org/login?url=https://research.morningstar.com/ic/ip-sign-in"
DOWNLOAD_WAIT_SECONDS = 90
PAGE_WAIT_SECONDS = 30

USERNAME_KEYWORDS = ("user",)  # ("barcode", "user", "username", "card")
PIN_KEYWORDS = ("pass",)  # ("pin", "password", "passcode", "pass")
DOWNLOAD_BUTTON_SELECTORS = (
    (By.CSS_SELECTOR, 'button[aria-label="Download CSV"]'),
    (By.XPATH, "//button[@value='Download CSV']"),
)


class AutoDownloadError(RuntimeError):
    """Raised when automated download fails."""


def download_compare_csvs(
    compare_links_path: Path,
    headless: bool = True,
    driver: Optional[Chrome] = None,
    download_dir: Optional[Path] = None,
) -> list[Path]:
    """
    Download Morningstar CSV exports listed in compare_links_path into a temp directory.

    If driver is provided, it must be an already-authenticated Chrome driver session
    (caller owns lifecycle and must quit it). If driver is None, creates and manages
    a new driver session. When driver is provided, download_dir must also be provided.

    Returns a list of CSV Paths in download order.
    """

    links = _load_links(compare_links_path)
    if not links:
        raise AutoDownloadError(
            f"No compare links were found in {compare_links_path}. Generate links first."
        )

    if driver is not None and download_dir is None:
        raise AutoDownloadError("download_dir must be provided when driver is provided")

    if driver is None:
        username = os.getenv("SPL_BARCODE")
        pin = os.getenv("SPL_PIN")
        if not username or not pin:
            raise AutoDownloadError(
                "SPL credentials missing. Set SPL_BARCODE and SPL_PIN in the environment or .env file."
            )

        download_dir = Path(tempfile.mkdtemp(prefix="ms_auto_", dir=None))
        console.print(f"[cyan]• Auto download directory:[/cyan] {download_dir}")

        with _managed_driver(download_dir, headless=headless) as d:
            perform_login(d, username, pin)
            return _do_downloads(d, links, download_dir)
    else:
        console.print(f"[cyan]• Auto download directory:[/cyan] {download_dir}")
        return _do_downloads(driver, links, download_dir)


def _do_downloads(driver: Chrome, links: list[str], download_dir: Path) -> list[Path]:
    """Download all links using the provided driver and download directory."""
    downloaded: list[Path] = []

    for idx, link in enumerate(links, start=1):
        console.print(f"[cyan]• Fetching link {idx}/{len(links)}[/cyan]")
        driver.get(link)
        _wait_for_page_ready(driver)
        button = _locate_download_button(driver)

        before = _existing_csv_names(download_dir)
        button.click()
        new_file = _wait_for_new_csv(download_dir, before)

        target = download_dir / f"morningstar_compare_{idx:03d}.csv"
        new_file.rename(target)
        downloaded.append(target)
        console.print(f"[green]  → Saved:[/green] {target}")

    return downloaded


def _load_links(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in content if line.strip()]


@contextmanager
def _managed_driver(download_dir: Path, headless: bool) -> Iterator[Chrome]:
    driver = build_driver(download_dir, headless=headless)
    try:
        yield driver
    finally:
        driver.quit()


def build_driver(download_dir: Path, headless: bool) -> Chrome:
    options = Options()
    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "safebrowsing.disable_download_protection": True,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1200")
    if headless:
        options.add_argument("--headless=new")

    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary

    driver_path = os.getenv("CHROMEDRIVER")
    if driver_path:
        service = Service(executable_path=driver_path)
    else:
        driver_path = _download_chromedriver()
        service = Service(executable_path=driver_path)

    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as exc:  # pragma: no cover - depends on local chrome install
        raise AutoDownloadError(f"Failed to initialize Chrome driver: {exc}") from exc
    return driver


def _download_chromedriver() -> str:
    try:
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError as exc:  # pragma: no cover - guard for optional dep
        raise AutoDownloadError(
            "webdriver-manager is required when CHROMEDRIVER is unset. "
            "Install it or specify CHROMEDRIVER env var."
        ) from exc

    try:
        return ChromeDriverManager().install()
    except Exception as exc:  # pragma: no cover - relies on network
        raise AutoDownloadError(
            f"Unable to locate ChromeDriver automatically: {exc}. "
            "Set CHROMEDRIVER to an existing driver path."
        ) from exc


def perform_login(driver: Chrome, username: str, pin: str) -> None:
    driver.get(LOGIN_URL)
    WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "form"))
    )

    username_field = _find_input(driver, USERNAME_KEYWORDS)
    pin_field = _find_input(driver, PIN_KEYWORDS)

    if not username_field:
        raise AutoDownloadError("Could not find the SPL barcode input on the EZProxy login page.")
    if not pin_field:
        raise AutoDownloadError("Could not find the SPL PIN input on the EZProxy login page.")

    username_field.clear()
    username_field.send_keys(username)

    pin_field.clear()
    pin_field.send_keys(pin)
    pin_field.send_keys(Keys.RETURN)

    def _logged_in(driver_ref: Chrome) -> bool:
        current = driver_ref.current_url.lower()
        if "ezproxy.spl.org" not in current:
            return True
        return "login" not in current

    try:
        WebDriverWait(driver, PAGE_WAIT_SECONDS).until(_logged_in)
    except TimeoutException as exc:
        raise AutoDownloadError(
            "Timed out waiting for EZProxy login to complete. Double-check credentials."
        ) from exc


def _find_input(driver: Chrome, keywords: Tuple[str, ...]) -> Optional[WebElement]:
    elements = driver.find_elements(By.CSS_SELECTOR, "input")
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)
    for element in elements:
        descriptor = " ".join(
            filter(
                None,
                [
                    element.get_attribute("id"),
                    element.get_attribute("name"),
                    element.get_attribute("placeholder"),
                    element.get_attribute("aria-label"),
                ],
            )
        ).lower()
        if any(keyword in descriptor for keyword in lowered_keywords):
            return element

    for element in elements:
        input_type = (element.get_attribute("type") or "").lower()
        if input_type in {"text", "password", ""}:
            return element
    return None


def _wait_for_page_ready(driver: Chrome) -> None:
    WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


def _locate_download_button(driver: Chrome) -> WebElement:
    last_exc: Optional[Exception] = None
    for selector in DOWNLOAD_BUTTON_SELECTORS:
        try:
            return WebDriverWait(driver, PAGE_WAIT_SECONDS).until(
                EC.element_to_be_clickable(selector)
            )
        except TimeoutException as exc:
            last_exc = exc
            continue
    raise AutoDownloadError(
        "Could not locate the 'Download CSV' button on the Morningstar compare page."
    ) from last_exc


def _existing_csv_names(download_dir: Path) -> set[str]:
    return {path.name for path in download_dir.glob("*.csv")}


def _wait_for_new_csv(download_dir: Path, previous: set[str]) -> Path:
    deadline = time.time() + DOWNLOAD_WAIT_SECONDS
    while time.time() < deadline:
        for path in download_dir.glob("*.csv"):
            if path.name in previous:
                continue
            return path
        time.sleep(1)
    raise AutoDownloadError("Timed out waiting for Morningstar CSV to finish downloading.")
