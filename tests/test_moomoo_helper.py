import pytest

from py_portfolio_index.exceptions import (
    ConfigurationError,
    ExtraAuthenticationStepException,
)
from py_portfolio_index.models import LoginResponseStatus
from py_portfolio_index.portfolio_providers.helpers import moomoo
from py_portfolio_index.portfolio_providers.helpers.moomoo import (
    DEFAULT_PORT,
    DEFAULT_TELNET_PORT,
    MooMooProxy,
    interactive_login,
)


class DummyProcess:
    pass


def test_start_proxy_requests_phone_code_and_raises_mfa(monkeypatch):
    commands = []

    class DummyPopen:
        def __init__(self, *args, **kwargs):
            pass

    def fake_wait_for_listening(port, address="localhost", timeout=30):
        return port == DEFAULT_TELNET_PORT

    def fake_check_listening(port, address="localhost", timeout=1):
        return port == DEFAULT_TELNET_PORT

    def fake_send_command(self, command, timeout=0.5):
        commands.append(command)
        if command == "show_sub_info":
            return "Need a phone verification code\nCommand tips: req_phone_verify_code"
        if command == "req_phone_verify_code":
            return "Request a phone verification code successfully\nCommand tips: input_phone_verify_code -code=123456"
        return ""

    monkeypatch.setattr(moomoo.subprocess, "Popen", DummyPopen)
    monkeypatch.setattr(moomoo, "wait_for_listening", fake_wait_for_listening)
    monkeypatch.setattr(moomoo, "check_listening", fake_check_listening)
    monkeypatch.setattr(MooMooProxy, "send_command", fake_send_command)

    proxy = MooMooProxy("OpenD.exe")

    with pytest.raises(ExtraAuthenticationStepException) as exc:
        proxy.start_proxy("OpenD.exe", "account", "password")

    assert proxy.mfa_in_progress is True
    assert exc.value.response.status == LoginResponseStatus.MFA_REQUIRED
    assert commands == ["show_sub_info", "req_phone_verify_code"]


def test_submit_mfa_waits_for_api_before_clearing_mfa(monkeypatch):
    commands = []

    def fake_send_command(self, command, timeout=0.5):
        commands.append(command)
        return "Total used quota:0,The remaining quota:100"

    monkeypatch.setattr(
        moomoo,
        "check_listening",
        lambda port, address="localhost", timeout=1: port == DEFAULT_TELNET_PORT,
    )
    monkeypatch.setattr(
        MooMooProxy, "_wait_for_api_auth_ready", lambda self, timeout=30: True
    )
    monkeypatch.setattr(MooMooProxy, "send_command", fake_send_command)

    proxy = MooMooProxy("OpenD.exe")
    proxy.process = DummyProcess()
    proxy.mfa_in_progress = True

    proxy.submit_mfa("123456")

    assert proxy.mfa_in_progress is False
    assert commands == ["input_phone_verify_code -code=123456"]


def test_submit_mfa_can_use_existing_telnet_connection(monkeypatch):
    commands = []

    def fake_check_listening(port, address="localhost", timeout=1):
        return port == DEFAULT_TELNET_PORT

    def fake_send_command(self, command, timeout=0.5):
        commands.append(command)
        return "Total used quota:0,The remaining quota:100"

    monkeypatch.setattr(moomoo, "check_listening", fake_check_listening)
    monkeypatch.setattr(
        MooMooProxy, "_wait_for_api_auth_ready", lambda self, timeout=30: True
    )
    monkeypatch.setattr(MooMooProxy, "send_command", fake_send_command)

    proxy = MooMooProxy("OpenD.exe")
    proxy.mfa_in_progress = True

    proxy.submit_mfa("123456")

    assert proxy.mfa_in_progress is False
    assert commands == ["input_phone_verify_code -code=123456"]


def test_submit_mfa_keeps_mfa_state_when_api_does_not_start(monkeypatch):
    monkeypatch.setattr(
        moomoo,
        "check_listening",
        lambda port, address="localhost", timeout=1: port == DEFAULT_TELNET_PORT,
    )
    monkeypatch.setattr(
        MooMooProxy, "_wait_for_api_auth_ready", lambda self, timeout=30: False
    )
    monkeypatch.setattr(
        MooMooProxy,
        "send_command",
        lambda self, command, timeout=0.5: "input_phone_verify_code -code=123456",
    )

    proxy = MooMooProxy("OpenD.exe")
    proxy.process = DummyProcess()
    proxy.mfa_in_progress = True

    with pytest.raises(ConfigurationError):
        proxy.submit_mfa("123456")

    assert proxy.mfa_in_progress is True


def test_interactive_login_checks_existing_opend_auth_state(monkeypatch):
    commands = []

    def fake_check_listening(port, address="localhost", timeout=1):
        return port in {DEFAULT_PORT, DEFAULT_TELNET_PORT}

    def fake_send_command(self, command, timeout=0.5):
        commands.append(command)
        if command == "show_sub_info":
            return "Need a phone verification code\nCommand tips: req_phone_verify_code"
        if command == "req_phone_verify_code":
            return "Command tips: input_phone_verify_code -code=123456"
        if command == "input_phone_verify_code -code=654321":
            return "Total used quota:0,The remaining quota:100"
        return ""

    monkeypatch.setattr(moomoo, "check_listening", fake_check_listening)
    monkeypatch.setattr(moomoo, "wait_for_listening", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        MooMooProxy, "_wait_for_api_auth_ready", lambda self, timeout=30: True
    )
    monkeypatch.setattr(MooMooProxy, "send_command", fake_send_command)
    monkeypatch.setattr("builtins.input", lambda prompt: "654321")

    proxy = interactive_login("OpenD.exe", "account", "password")

    assert proxy.mfa_in_progress is False
    assert commands == [
        "show_sub_info",
        "req_phone_verify_code",
        "input_phone_verify_code -code=654321",
    ]


def test_interactive_login_uses_sdk_probe_when_telnet_has_no_mfa_marker(monkeypatch):
    commands = []

    def fake_check_listening(port, address="localhost", timeout=1):
        return port in {DEFAULT_PORT, DEFAULT_TELNET_PORT}

    def fake_send_command(self, command, timeout=0.5):
        commands.append(command)
        if command == "show_sub_info":
            return "Total used quota:0,The remaining quota:100"
        if command == "input_phone_verify_code -code=654321":
            return "Total used quota:0,The remaining quota:100"
        return ""

    auth_ready_results = iter([False, True])

    monkeypatch.setattr(moomoo, "check_listening", fake_check_listening)
    monkeypatch.setattr(
        MooMooProxy, "_is_api_auth_ready", lambda self: next(auth_ready_results)
    )
    monkeypatch.setattr(
        MooMooProxy, "_wait_for_api_auth_ready", lambda self, timeout=30: True
    )
    monkeypatch.setattr(MooMooProxy, "send_command", fake_send_command)
    monkeypatch.setattr("builtins.input", lambda prompt: "654321")

    proxy = interactive_login("OpenD.exe", "account", "password")

    assert proxy.mfa_in_progress is False
    assert commands == [
        "show_sub_info",
        "input_phone_verify_code -code=654321",
    ]
