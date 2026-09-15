"""Container / local-server settings (HTTP only, bound to localhost)."""
from .base import *  # noqa: F401,F403

DEBUG = False

# Serve collectstatic output (admin / DRF browsable API assets) from the app.
MIDDLEWARE = [*MIDDLEWARE]
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
