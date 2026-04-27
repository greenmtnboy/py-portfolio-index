import socket as socket
import subprocess
import time
from py_portfolio_index.exceptions import (
    ConfigurationError,
    ExtraAuthenticationStepException,
)
from py_portfolio_index.models import LoginResponse, LoginResponseStatus
from py_portfolio_index.portfolio_providers.helpers.telnetlib import Telnet

DEFAULT_PORT = 11111
DEFAULT_TELNET_PORT = 22222
DEFAULT_TELNET_IP = "127.0.0.1"


STARTUP_TIMEOUT = 30
POLL_INTERVAL = 0.5
TELNET_READ_TIMEOUT = 0.5
API_AUTH_TIMEOUT = 2


MFA_MARKERS = (
    "Need a phone verification code",
    "Command tips: req_phone_verify_code",
    "Command tips: input_phone_verify_code",
)
PHONE_CODE_REQUEST_MARKER = "Command tips: req_phone_verify_code"
PHONE_CODE_INPUT_MARKER = "Command tips: input_phone_verify_code"
FAILURE_MARKERS = (
    "Login failed",
    "Login Fail",
    "login failed",
    "incorrect password",
)


def check_listening(port: int, address: str = "localhost", timeout: float = 1):
    s = socket.socket()
    try:
        s.settimeout(timeout)
        s.connect((address, port))
        return True
    except socket.error:
        return False
    finally:
        s.close()


def wait_for_listening(
    port: int,
    address: str = "localhost",
    timeout: float = STARTUP_TIMEOUT,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_listening(port, address=address):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def interactive_login(
    opend_path: str, account: str, pwd: str, lang: str = "en"
) -> "MooMooProxy":
    proxy = MooMooProxy(opend_path)
    try:
        if check_listening(DEFAULT_PORT) and check_listening(
            proxy.telnet_port, address=proxy.telnet_ip
        ):
            proxy._wait_for_proxy_ready()
        elif check_listening(DEFAULT_PORT):
            return proxy
        else:
            proxy.start_proxy(opend_path, account, pwd, lang)
    except ExtraAuthenticationStepException:
        factor = input("Input factor: ")
        proxy.submit_mfa(factor)
    proxy.validate(account, pwd)
    return proxy


class MooMooProxy:
    def __init__(self, opend_path: str | None = None):
        self.opend_path = opend_path
        self.process: subprocess.Popen | None = None
        self.lang = "en"
        self.mfa_in_progress = False
        self.telnet_port = DEFAULT_TELNET_PORT
        self.telnet_ip = DEFAULT_TELNET_IP

    def validate(
        self, account: str | None, pwd: str | None, extra_factor: str | None = None
    ) -> bool:
        if check_listening(DEFAULT_PORT):
            return True
        if not self.process and self.opend_path and account and pwd:
            self.start_proxy(self.opend_path, account, pwd, self.lang)
        if self.mfa_in_progress and extra_factor:
            self.submit_mfa(extra_factor)
        return check_listening(DEFAULT_PORT)

    def submit_mfa(self, code: str):
        if not self.mfa_in_progress:
            return
        if not check_listening(self.telnet_port, address=self.telnet_ip):
            raise ConfigurationError(
                "MFA is in progress, but no connection is available to submit the code."
            )
        output = self.send_command(f"input_phone_verify_code -code={code}")
        if self._is_login_failure(output):
            raise ConfigurationError(output)
        if not self._wait_for_api_auth_ready(timeout=STARTUP_TIMEOUT):
            raise ConfigurationError(
                f"MFA code was submitted, but moomoo OpenD API did not start. Response: {output}"
            )
        self.mfa_in_progress = False

    def start_proxy(
        self, path: str, login_account: str, login_pwd: str, lang: str = "en"
    ) -> bool:
        self.process = subprocess.Popen(
            [
                path,
                f"-login_account={login_account}",
                f"-login_pwd={login_pwd}",
                f"-lang={lang}",
                f"-telnet_ip={self.telnet_ip}",
                f"-telnet_port={self.telnet_port}",
                "-console=1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not wait_for_listening(
            self.telnet_port, address=self.telnet_ip, timeout=STARTUP_TIMEOUT
        ):
            raise ConfigurationError(
                f"moomoo OpenD telnet console did not start on {self.telnet_ip}:{self.telnet_port}."
            )
        self._wait_for_proxy_ready()
        return self.validate(login_account, login_pwd)

    def _wait_for_proxy_ready(self) -> bool:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        last_output = ""
        while time.monotonic() < deadline:
            if check_listening(self.telnet_port, address=self.telnet_ip):
                last_output = self.send_command("show_sub_info")
                if self._is_login_failure(last_output):
                    raise ConfigurationError(last_output)
                if self._is_mfa_required(last_output):
                    self._request_phone_verify_code(last_output)
            if check_listening(DEFAULT_PORT):
                if self._is_api_auth_ready():
                    return True
                if check_listening(self.telnet_port, address=self.telnet_ip):
                    self._raise_mfa_required(last_output)
            time.sleep(POLL_INTERVAL)

        code_request_output = self.send_command("req_phone_verify_code")
        combined_output = f"{last_output}\n{code_request_output}"
        if self._is_mfa_required(combined_output):
            self._raise_mfa_required(combined_output)
        raise ConfigurationError(
            f"moomoo OpenD API did not start on localhost:{DEFAULT_PORT}. Last telnet response: {combined_output}"
        )

    def _wait_for_api_auth_ready(self, timeout: float = API_AUTH_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_api_auth_ready():
                return True
            time.sleep(POLL_INTERVAL)
        return False

    def _is_api_auth_ready(self) -> bool:
        if not check_listening(DEFAULT_PORT):
            return False
        try:
            from moomoo import ContextStatus, OpenQuoteContext

            context = OpenQuoteContext(
                host="localhost",
                port=DEFAULT_PORT,
                is_async_connect=True,
            )
            try:
                deadline = time.monotonic() + API_AUTH_TIMEOUT
                while time.monotonic() < deadline:
                    if context.status == ContextStatus.READY:
                        return True
                    time.sleep(0.1)
                return False
            finally:
                context.close()
        except Exception:
            return False

    def _request_phone_verify_code(self, observed_output: str):
        request_output = ""
        if (
            PHONE_CODE_REQUEST_MARKER in observed_output
            and PHONE_CODE_INPUT_MARKER not in observed_output
        ):
            request_output = self.send_command("req_phone_verify_code")
        self._raise_mfa_required(f"{observed_output}\n{request_output}")

    def _raise_mfa_required(self, output: str):
        self.mfa_in_progress = True
        raise ExtraAuthenticationStepException(
            response=LoginResponse(
                status=LoginResponseStatus.MFA_REQUIRED,
                data={"message": output},
            )
        )

    @staticmethod
    def _is_mfa_required(output: str) -> bool:
        return any(marker in output for marker in MFA_MARKERS)

    @staticmethod
    def _is_login_failure(output: str) -> bool:
        return any(marker in output for marker in FAILURE_MARKERS)

    def send_command(self, command: str, timeout: float = TELNET_READ_TIMEOUT):
        with Telnet(
            self.telnet_ip, self.telnet_port
        ) as tn:  # Telnet address is: 127.0.0.1, Telnet port is: 22222
            tn.write(command.encode() + b"\r\n")
            reply = b""
            while True:
                msg = tn.read_until(b"\r\n", timeout=timeout)
                reply += msg
                if msg == b"":
                    break
            output = reply.decode("gb2312")
        return output

    def close(self):
        if self.process:
            self.send_command("exit")
            self.process = None

    def connect(self, account: str, pwd: str):
        if not self.opend_path:
            raise ValueError(
                "Proxy must be given an OpenD path to automatically start a proxy; if you do not want to do this, ensure a MooMoo OpenD proxy is already running."
            )
            # run this command in a subprocess

        return self.start_proxy(self.opend_path, account, pwd)
