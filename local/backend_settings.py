"""Actual msg-backend settings with HTTP enabled only for this localhost lab."""
from config.settings import *  # noqa: F403

SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
DATABASES["default"]["OPTIONS"] = {"connect_timeout": 2}  # noqa: F405
CACHES["default"]["OPTIONS"].update(  # noqa: F405
    SOCKET_CONNECT_TIMEOUT=2,
    SOCKET_TIMEOUT=2,
)
