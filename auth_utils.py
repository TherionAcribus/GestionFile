from __future__ import annotations

from functools import wraps

import jwt
from flask import current_app, has_request_context, jsonify, request

try:
    from flask_security import current_user
except Exception:  # pragma: no cover
    current_user = None


def verify_app_token(token: str) -> bool:
    if not token:
        return False
    try:
        jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def is_authenticated_request() -> bool:
    if not has_request_context():
        return False

    token = request.headers.get("X-App-Token")
    if token and verify_app_token(token):
        return True

    if current_user is None:
        return False

    return bool(getattr(current_user, "is_authenticated", False))


def require_app_token_or_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_authenticated_request():
            return func(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401

    return wrapper
