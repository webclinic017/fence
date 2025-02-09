import authutils.errors
import authutils.token.keys
import authutils.token.validate
import jwt

from fence.config import config
from fence.errors import Unauthorized
from fence.jwt.blacklist import is_blacklisted
from fence.jwt.errors import JWTError, JWTPurposeError
from fence.jwt.utils import get_jwt_header


def validate_purpose(claims, pur):
    """
    Check that the claims from a JWT have the expected purpose ``pur``

    Args:
        claims (dict): claims from token
        pur (str): expected purpose

    Return:
        None

    Raises:
        JWTPurposeError:
            if the claims do not contain a purpose claim or if it doesn't match
            the expected value
    """
    if "pur" not in claims:
        raise JWTPurposeError("claims missing `pur` claim")
    if claims["pur"] != pur:
        raise JWTPurposeError(
            "claims have incorrect purpose: expected {}, got {}".format(
                pur, claims["pur"]
            )
        )


def validate_jwt(
    encoded_token=None,
    aud=None,
    scope={"openid"},
    require_purpose=True,
    purpose=None,
    public_key=None,
    attempt_refresh=False,
    issuers=None,
    pkey_cache=None,
    **kwargs
):
    """
    Validate a JWT and return the claims.

    This wraps the authutils functions to work correctly for fence and
    correctly validate the token. Other functions in fence should call this
    function and not use any functions from authutils.

    Args:
        encoded_token (str): the base64 encoding of the token
        aud (Optional[str]):
            audience as which the app identifies, which the JWT will be
            expected to include in its ``aud`` claim.
            Optional; will default to issuer (config["BASE_URL"]).
            To skip aud validation, pass the following as a kwarg:
              options={"verify_aud": False}
        scope (Optional[Iterable[str]]):
            list of scopes each of which the token must satisfy; defaults
            to ``{'openid'}`` (minimum expected by OpenID provider).
            Explicitly set this to None to skip scope validation.
        purpose (Optional[str]):
            which purpose the token is supposed to be used for (access,
            refresh, or id)
        public_key (Optional[str]): public key to vaidate JWT with

    Return:
        dict: dictionary of claims from the validated JWT

    Raises:
        JWTError:
            if auth header is missing, decoding fails, or the JWT fails to
            satisfy any expectation
    """

    if encoded_token is None:
        try:
            encoded_token = get_jwt_header()
        except Unauthorized as e:
            raise JWTError(e.message)

    assert (
        isinstance(scope, set) or isinstance(scope, list) or scope is None
    ), "scope argument must be set or list or None"

    # Can't set arg default to config[x] in fn def, so doing it this way.
    if aud is None:
        aud = config["BASE_URL"]

    iss = config["BASE_URL"]
    if issuers is None:
        issuers = [iss]
        oidc_iss = (
            config.get("OPENID_CONNECT", {}).get("fence", {}).get("api_base_url", None)
        )
        if oidc_iss:
            issuers.append(oidc_iss)
    try:
        token_iss = jwt.decode(encoded_token, verify=False).get("iss")
    except jwt.InvalidTokenError as e:
        raise JWTError(e)
    attempt_refresh = attempt_refresh and (token_iss != iss)
    public_key = public_key or authutils.token.keys.get_public_key_for_token(
        encoded_token, attempt_refresh=attempt_refresh, pkey_cache=pkey_cache
    )

    try:
        claims = authutils.token.validate.validate_jwt(
            encoded_token=encoded_token,
            aud=aud,
            scope=scope,
            purpose=purpose,
            issuers=issuers,
            public_key=public_key,
            attempt_refresh=attempt_refresh,
            **kwargs
        )
    except authutils.errors.JWTError as e:

        ##### begin refresh token and API key patch block #####
        # TODO: In the next release, remove this if/elif block and take the else block
        # back out of the else.
        # Old refresh tokens and API keys are not compatible with new validation, so to smooth
        # the transition, allow old style refresh tokens/API keys with this patch;
        # remove patch in next tag. Refresh tokens and API keys have default TTL of 30 days.
        from authutils.errors import JWTAudienceError

        unverified_claims = jwt.decode(encoded_token, verify=False)
        if unverified_claims.get("pur") == "refresh" and isinstance(
            e, JWTAudienceError
        ):
            # Check everything else is fine minus the audience
            try:
                claims = authutils.token.validate.validate_jwt(
                    encoded_token=encoded_token,
                    aud="openid",
                    scope=None,
                    purpose="refresh",
                    issuers=issuers,
                    public_key=public_key,
                    attempt_refresh=attempt_refresh,
                    **kwargs
                )
            except Error as e:
                raise JWTError("Invalid refresh token: {}".format(e))
        elif unverified_claims.get("pur") == "api_key" and isinstance(
            e, JWTAudienceError
        ):
            # Check everything else is fine minus the audience
            try:
                claims = authutils.token.validate.validate_jwt(
                    encoded_token=encoded_token,
                    aud="fence",
                    scope=None,
                    purpose="api_key",
                    issuers=issuers,
                    public_key=public_key,
                    attempt_refresh=attempt_refresh,
                    **kwargs
                )
            except Error as e:
                raise JWTError("Invalid API key: {}".format(e))
        else:
            ##### end refresh token, API key patch block #####
            msg = "Invalid token : {}".format(str(e))
            unverified_claims = jwt.decode(encoded_token, verify=False)
            if not unverified_claims.get("scope") or "" in unverified_claims["scope"]:
                msg += "; was OIDC client configured with scopes?"
            raise JWTError(msg)
    if purpose:
        validate_purpose(claims, purpose)
    if require_purpose and "pur" not in claims:
        raise JWTError("token {} missing purpose (`pur`) claim".format(claims["jti"]))

    # For refresh tokens and API keys specifically, check that they are not
    # blacklisted.
    if require_purpose and (claims["pur"] == "refresh" or claims["pur"] == "api_key"):
        if is_blacklisted(claims["jti"]):
            raise JWTError("token is blacklisted")

    return claims


def require_jwt(aud=None, purpose=None):
    def decorator(f):
        def wrapper(*args, **kwargs):

            validate_jwt(aud=aud, purpose=purpose)
            return f(args, kwargs)

        return wrapper

    return decorator
