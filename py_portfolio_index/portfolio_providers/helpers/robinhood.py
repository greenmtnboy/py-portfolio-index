# Modified version of robin_stocks login to enable asynchronous flows
# such as in a web server

import os
import pickle

import platformdirs
from pathlib import Path
from py_portfolio_index.exceptions import (
    ExtraAuthenticationStepException,
    ConfigurationError,
)
from py_portfolio_index.models import LoginResponse, LoginResponseStatus

ROBINHOOD_USERNAME_ENV = "ROBINHOOD_USERNAME"
ROBINHOOD_PASSWORD_ENV = "ROBINHOOD_PASSWORD"


def validate_login():
    from robin_stocks.robinhood.authentication import (
        request_get,
        positions_url,
    )

    res = request_get(
        positions_url(),
        "pagination",
        {"nonzero": "true"},
        jsonify_data=False,
    )
    # Raises exception is response code is not 200.
    try:
        res.raise_for_status()
    except Exception:
        raise ConfigurationError()


def login(
    username=None,
    password=None,
    expiresIn=86400,
    scope="internal",
    by_sms=True,
    store_session=True,
    challenge_response: str | int | None = None,
    pickle_name="",
    prior_response=LoginResponse | None,
):
    from robin_stocks.robinhood.authentication import (
        generate_device_token,
        login_url,
        update_session,
        set_login_state,
        respond_to_challenge,
        request_get,
        positions_url,
        request_post,
    )

    """This function will effectively log the user into robinhood by getting an
    authentication token and saving it to the session header. By default, it
    will store the authentication token in a pickle file and load that value
    on subsequent logins.

    :param username: The username for your robinhood account, usually your email.
        Not required if credentials are already cached and valid.
    :type username: Optional[str]
    :param password: The password for your robinhood account. Not required if
        credentials are already cached and valid.
    :type password: Optional[str]
    :param expiresIn: The time until your login session expires. This is in seconds.
    :type expiresIn: Optional[int]
    :param scope: Specifies the scope of the authentication.
    :type scope: Optional[str]
    :param by_sms: Specifies whether to send an email(False) or an sms(True)
    :type by_sms: Optional[boolean]
    :param store_session: Specifies whether to save the log in authorization
        for future log ins.
    :type store_session: Optional[boolean]
    :param mfa_code: MFA token if enabled.
    :type mfa_code: Optional[str]
    :param pickle_name: Allows users to name Pickle token file in order to switch
        between different accounts without having to re-login every time.
    :returns:  A dictionary with log in information. The 'access_token' keyword contains the access token, and the 'detail' keyword \
    contains information on whether the access token was generated or loaded from pickle file.

    """
    if not username:
        username = os.environ.get(ROBINHOOD_USERNAME_ENV, None)
    if not password:
        password = os.environ.get(ROBINHOOD_PASSWORD_ENV, None)
    device_token = generate_device_token()
    data_dir = Path(
        platformdirs.user_data_dir("py_portfolio_index", ensure_exists=True)
    )
    data_dir = data_dir / ".tokens"
    creds_file = "robinhood" + pickle_name + ".pickle"
    pickle_path = data_dir / creds_file
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    # Challenge type is used if not logging in with two-factor authentication.
    if by_sms:
        challenge_type = "sms"
    else:
        challenge_type = "email"

    url = login_url()
    payload = {
        "client_id": "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS",
        "expires_in": expiresIn,
        "grant_type": "password",
        "password": password,
        "scope": scope,
        "username": username,
        "challenge_type": challenge_type,
        "device_token": device_token,
    }

    # If authentication has been stored in pickle file then load it. Stops login server from being pinged so much.
    if os.path.isfile(pickle_path):
        # If store_session has been set to false then delete the pickle file, otherwise try to load it.
        # Loading pickle file will fail if the access_token has expired.
        if store_session:
            try:
                with open(pickle_path, "rb") as f:
                    pickle_data = pickle.load(f)
                    access_token = pickle_data["access_token"]
                    token_type = pickle_data["token_type"]
                    refresh_token = pickle_data["refresh_token"]
                    # Set device_token to be the original device token when first logged in.
                    pickle_device_token = pickle_data["device_token"]
                    payload["device_token"] = pickle_device_token
                    # Set login status to True in order to try and get account info.
                    set_login_state(True)
                    update_session(
                        "Authorization", "{0} {1}".format(token_type, access_token)
                    )
                    # Try to load account profile to check that authorization token is still valid.
                    res = request_get(
                        positions_url(),
                        "pagination",
                        {"nonzero": "true"},
                        jsonify_data=False,
                    )
                    # Raises exception is response code is not 200.
                    res.raise_for_status()
                    return {
                        "access_token": access_token,
                        "token_type": token_type,
                        "expires_in": expiresIn,
                        "scope": scope,
                        "detail": "logged in using authentication in {0}".format(
                            creds_file
                        ),
                        "backup_code": None,
                        "refresh_token": refresh_token,
                    }
            except Exception as e:
                print(
                    f"ERROR: There was an issue loading pickle file {str(e)}. Authentication may be expired - logging in normally."
                )
                set_login_state(False)
                update_session("Authorization", None)
                # raise ConfigurationError()
        else:
            os.remove(pickle_path)

    # Handle case where mfa or challenge is required.
    if prior_response:
        if prior_response.status == LoginResponseStatus.MFA_REQUIRED:
            assert challenge_response is not None
            payload["mfa_code"] = challenge_response
        if prior_response.status == LoginResponseStatus.CHALLENGE_REQUIRED:
            res = respond_to_challenge(
                prior_response.data["challenge_id"], challenge_response
            )
            update_session(
                "X-ROBINHOOD-CHALLENGE-RESPONSE-ID", prior_response.data["challenge_id"]
            )
    data = request_post(url, payload)

    if data:
        if "mfa_required" in data:
            raise ExtraAuthenticationStepException(
                response=LoginResponse(status=LoginResponseStatus.MFA_REQUIRED)
            )
        elif "challenge" in data:
            challenge_id = data["challenge"]["id"]
            raise ExtraAuthenticationStepException(
                response=LoginResponse(
                    status=LoginResponseStatus.CHALLENGE_REQUIRED,
                    data={"challenge_id": challenge_id},
                )
            )
        # Update Session data with authorization or raise exception with the information present in data.
        if "access_token" in data:
            token = "{0} {1}".format(data["token_type"], data["access_token"])
            update_session("Authorization", token)
            set_login_state(True)
            data["detail"] = "logged in with brand new authentication code."
            if store_session:
                with open(pickle_path, "wb") as f:
                    pickle.dump(
                        {
                            "token_type": data["token_type"],
                            "access_token": data["access_token"],
                            "refresh_token": data["refresh_token"],
                            "device_token": payload["device_token"],
                        },
                        f,
                    )
        else:
            raise Exception(data["detail"])
    else:
        raise Exception(
            "Error: Trouble connecting to robinhood API. Check internet connection."
        )
    return data
