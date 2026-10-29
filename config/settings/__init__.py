"""
Routes to the right settings module based on DJANGO_ENV, so
DJANGO_SETTINGS_MODULE can stay "config.settings" everywhere
(manage.py, wsgi.py, deployment platform, etc.) and you only ever
flip one env var to switch environments.

Set DJANGO_ENV=production on your deployment platform (e.g. Render).
Leave it unset, or set it to "local", for development.
"""

import os

DJANGO_ENV = os.getenv("DJANGO_ENV", "local").lower()


if DJANGO_ENV == "production":
    from .production import *  
else:
    from .local import *  