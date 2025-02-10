import socket as socket
import subprocess
from time import sleep
from py_portfolio_index.models import LoginResponse, LoginResponseStatus
from py_portfolio_index.exceptions import (
    ConfigurationError,
    ExtraAuthenticationStepException,
)
from py_portfolio_index.portfolio_providers.helpers.telnetlib import Telnet

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

def interactive_login(opend_path: str, account: str, pwd: str, lang: str = "en") -> bool:
    proxy = MooMooProxy(opend_path)
    try:
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
        self.telnet_port = 22222
        self.telnet_ip = "127.0.0.1"

    def validate(self, account: str, pwd: str, extra_factor: str | None = None) -> True:
        if not self.process:
            self.start_proxy(self.opend_path, account, pwd, self.lang)
        if self.mfa_in_progress:
            self.submit_mfa(extra_factor)
        if check_listening(DEFAULT_PORT):
            return True
        return self.connect(account, pwd)

    def submit_mfa(self, code: str):
        if not self.mfa_in_progress:
            return
        if not self.process:
            raise ConfigurationError(
                "MFA is in progress, but no connection is available to submit the code."
            )
        self.send_command(f"input_phone_verify_code -code={code}")
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
                "-console=1"
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        results = self.send_command("show_sub_info")

        # raise ExtraAuthenticationStepException(
        #     response=LoginResponse(status=LoginResponseStatus.MFA_REQUIRED)
        # )
        self.validate(login_account, login_pwd)
    

    def send_command(self, command: str):
        with Telnet(self.telnet_ip, self.telnet_port) as tn:  # Telnet address is: 127.0.0.1, Telnet port is: 22222
            tn.write(command.encode()+b'\r\n')
            reply = b''
            while True:
                msg = tn.read_until(b'\r\n', timeout=0.5)
                reply += msg
                if msg == b'':
                    break
            output = reply.decode('gb2312')
            print(output)
        return output
    
    def close(self):
        if self.process:
            self.send_command('exit')
            self.process = None

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
