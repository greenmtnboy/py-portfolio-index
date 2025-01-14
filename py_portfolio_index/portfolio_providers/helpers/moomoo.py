import socket as socket
import subprocess
from time import sleep
from py_portfolio_index.models import LoginResponse, LoginResponseStatus
from py_portfolio_index.exceptions import (
    ConfigurationError,
    ExtraAuthenticationStepException,
)

DEFAULT_PORT = 11111


def check_listening(port: int):
    # Create a TCP socket
    address = "localhost"
    s = socket.socket()
    try:
        s.connect((address, port))
        return True
    except socket.error:
        return False
    finally:
        s.close()


class MooMooProxy:
    def __init__(self, opend_path: str | None = None):
        self.opend_path = opend_path
        self.connection: subprocess.Popen | None = None
        self.lang = "en"
        self.mfa_in_progress = False

    def validate(self, account: str, pwd: str, extra_factor: str | None = None) -> True:
        if self.mfa_in_progress:
            self.submit_mfa(extra_factor)
        if check_listening(DEFAULT_PORT):
            return True
        return self.connect(account, pwd)

    def submit_mfa(self, code: str):
        if not self.mfa_in_progress:
            return
        if not self.connection:
            raise ConfigurationError(
                "MFA is in progress, but no connection is available to submit the code."
            )
        self.connection.stdin.write(f"input_phone_verify_code -code={code}\n")
        self.connection.stdin.flush()
        self.mfa_in_progress = False

    def start_proxy(
        self, path: str, login_account: str, login_pwd: str, lang: str = "en"
    ) -> bool:
        # run this command in a subprocess
        # -login_account=100000 -login_pwd=123456 -lang=en

        self.process = subprocess.Popen(
            [
                path,
                f"-login_account={login_account}",
                f"-login_pwd={login_pwd}",
                f"-lang={lang}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,  # Ensures strings (not bytes) are used)
        )
        initial_output = self.process.stdout.readline().strip()
        if "Login successful" in initial_output:
            return True
        if "input_phone_verify_code" in initial_output:
            self.mfa_in_progress = True
            raise ExtraAuthenticationStepException(
                response=LoginResponse(status=LoginResponseStatus.MFA_REQUIRED)
            )
        raise ConfigurationError(f"Could not start moomoo proxy; {self.process.stderr}")

    def close(self):
        if self.connection:
            self.connection.terminate()
            self.connection = None

    def connect(self, account: str, pwd: str):
        if not self.opend_path:
            raise ValueError(
                "Proxy must be given an OpenD path to automatically start a proxy; if you do not want to do this, ensure a MooMoo OpenD proxy is already running."
            )
            # run this command in a subprocess

        process = subprocess.Popen(
            [
                self.opend_path,
                f"-login_account={account}",
                f"-login_pwd={pwd}",
                f"-lang={self.lang}",
            ]
        )
        process.communicate()
        for _ in range(0, 10):
            if check_listening(DEFAULT_PORT):
                return process
            sleep(5)

        raise ConfigurationError(f"Could not start moomoo proxy; {process.stderr}")
