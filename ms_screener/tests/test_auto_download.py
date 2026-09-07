"""Tests for auto_download login handling."""

import pytest

from ms_screener.src import auto_download
from ms_screener.src.auto_download import AutoDownloadError, perform_login

REJECTION_BODY = (
    "Seattle Public Library\n"
    "Sorry, your Library Card Number or PIN is not recognized, or your account "
    "is suspended. Please try again or contact us for help with your account.\n"
    "Contact us"
)


class FakeElement:
    """Minimal WebElement stand-in for the login form fields and body."""

    def __init__(self, attributes=None, text=""):
        self._attributes = attributes or {}
        self.text = text
        self.keys_sent = []

    def get_attribute(self, name):
        return self._attributes.get(name)

    def clear(self):
        pass

    def send_keys(self, value):
        self.keys_sent.append(value)


class FakeDriver:
    """Driver double that serves a login form and a scripted post-submit page."""

    def __init__(self, body_text="", url_after_submit="https://ezproxy.spl.org/login"):
        self.current_url = "https://ezproxy.spl.org/login"
        self._body_text = body_text
        self._url_after_submit = url_after_submit
        self.submitted = False

    def get(self, url):
        self.current_url = url

    def find_elements(self, by, value):
        if value == "form":
            return [FakeElement()]
        if value == "input":
            return [
                FakeElement({"name": "user"}),
                FakeElement({"name": "pass"}),
            ]
        return []

    def find_element(self, by, value):
        if value == "body":
            # The body only carries the rejection text once the form was posted.
            return FakeElement(text=self._body_text if self.submitted else "")
        if value == "form":
            return FakeElement()
        raise AssertionError(f"unexpected find_element({value})")


class SubmittingDriver(FakeDriver):
    """Marks the submit and applies the post-submit URL when RETURN is sent."""

    def find_elements(self, by, value):
        elements = super().find_elements(by, value)
        if value == "input":
            for element in elements:
                element.send_keys = self._make_send_keys(element)
        return elements

    def _make_send_keys(self, element):
        def send_keys(value):
            element.keys_sent.append(value)
            if value == "":  # Keys.RETURN
                self.submitted = True
                self.current_url = self._url_after_submit
        return send_keys


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch):
    """Keep the timeout branch from actually blocking for 30 seconds."""
    monkeypatch.setattr(auto_download, "PAGE_WAIT_SECONDS", 0)


class TestPerformLogin:
    def test_rejection_message_surfaces_verbatim(self):
        driver = SubmittingDriver(body_text=REJECTION_BODY)

        with pytest.raises(AutoDownloadError) as excinfo:
            perform_login(driver, "barcode", "pin")

        message = str(excinfo.value)
        assert "Library Card Number or PIN is not recognized" in message
        assert "SPL_BARCODE/SPL_PIN" in message
        assert "Timed out" not in message

    def test_successful_login_does_not_raise(self):
        driver = SubmittingDriver(
            url_after_submit="https://research.morningstar.com/ic/ip-sign-in"
        )

        perform_login(driver, "barcode", "pin")

    def test_timeout_wording_preserved_without_error_text(self):
        driver = SubmittingDriver(body_text="Enter your Library Card Number and PIN.")

        with pytest.raises(AutoDownloadError) as excinfo:
            perform_login(driver, "barcode", "pin")

        assert "Timed out waiting for EZProxy login to complete." in str(excinfo.value)


class TestLoginErrorText:
    def test_returns_only_the_offending_sentence(self):
        driver = FakeDriver(body_text=REJECTION_BODY)
        driver.submitted = True

        text = auto_download._login_error_text(driver)

        assert text == (
            "Sorry, your Library Card Number or PIN is not recognized, or your "
            "account is suspended."
        )

    def test_returns_none_on_clean_page(self):
        driver = FakeDriver(body_text="Welcome to the Seattle Public Library.")
        driver.submitted = True

        assert auto_download._login_error_text(driver) is None
