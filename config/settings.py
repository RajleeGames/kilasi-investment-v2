import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# SIMPLE .ENV LOADER
# ============================================================
# Reads BASE_DIR/.env without requiring python-dotenv.
# Existing OS/systemd environment variables always win.

def _load_env_file(path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return int(default)


# ============================================================
# SECURITY
# ============================================================

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-only-change-this-secret-key",
)

DEBUG = env_bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = [
    "kilasiinvestment.store",
    "www.kilasiinvestment.store",
    "127.0.0.1",
    "localhost",
]

CSRF_TRUSTED_ORIGINS = [
    "https://kilasiinvestment.store",
    "https://www.kilasiinvestment.store",
]


# Useful when Django is behind Nginx
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Keep False while testing locally. In production set DJANGO_DEBUG=0
# and change SESSION_COOKIE_SECURE below to True.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

SECURE_REFERRER_POLICY = "same-origin"


# ============================================================
# APPLICATIONS
# ============================================================

INSTALLED_APPS = [
    'kilasi_orders.apps.KilasiOrdersConfig',  # KILASI_V2_PHASE6B_ORDERS
    'kilasi_trade.apps.KilasiTradeConfig',  # KILASI_V2_PHASE6A_TRADE
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "core",
    "kilasi_billing",
    "kilasi_transport",
    "sms_center",
]


# ============================================================
# MIDDLEWARE
# ============================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "kilasi_transport.middleware.TransportRouteFirewall",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# URLS
# ============================================================

ROOT_URLCONF = "config.urls"


# ============================================================
# TEMPLATES
# ============================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [
            BASE_DIR / "templates",
        ],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",

                "core.context_processors.shop_context",
                "kilasi_transport.context_processors.transport_role",
            ],
        },
    },
]


# ============================================================
# WSGI
# ============================================================

WSGI_APPLICATION = "config.wsgi.application"


# ============================================================
# DATABASE
# ============================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",

        "NAME": os.getenv(
    "KILASI_DB_PATH",
    str(BASE_DIR / "db.sqlite3"),
),
    }
}


# ============================================================
# PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },

    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },

    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },

    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ============================================================
# LANGUAGE / TIME
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Dar_es_Salaam"

USE_I18N = True

USE_TZ = True


# ============================================================
# STATIC FILES
# ============================================================

STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"


# ============================================================
# MEDIA FILES
# ============================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"


# ============================================================
# AUTHENTICATION
# ============================================================

LOGIN_URL = "login"

LOGIN_REDIRECT_URL = "dashboard"

LOGOUT_REDIRECT_URL = "login"


# ============================================================
# DJANGO DEFAULTS
# ============================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# FILE UPLOAD SAFETY
# ============================================================

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024


# ============================================================
# SESSION
# ============================================================

SESSION_COOKIE_AGE = 60 * 60 * 12

SESSION_SAVE_EVERY_REQUEST = True


# ============================================================
# EMAIL
# ============================================================

# No email service configured yet.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# ============================================================
# QZ TRAY - DEVELOPMENT / PRODUCTION
# ============================================================

QZ_KEYS_DIR = Path(
    os.getenv(
        "QZ_KEYS_DIR",
        str(BASE_DIR / "qz_keys"),
    )
).expanduser().resolve()


QZ_CERT_PATH = Path(
    os.getenv(
        "QZ_CERT_PATH",
        str(QZ_KEYS_DIR / "digital-certificate.txt"),
    )
).expanduser().resolve()


QZ_PRIVATE_KEY_PATH = Path(
    os.getenv(
        "QZ_PRIVATE_KEY_PATH",
        str(QZ_KEYS_DIR / "private-key.pem"),
    )
).expanduser().resolve()


QZ_SIGNATURE_ALGORITHM = "SHA512"

QZ_MAX_SIGNING_BYTES = 1024 * 1024


# Leave empty during first test if you want JavaScript auto detection.
QZ_PRINTER_NAME = (
    os.getenv(
        "QZ_PRINTER_NAME",
        "XP-80C",
    ).strip()
)


# Development debug
if DEBUG:
    print("[QZ] KEYS DIR:", QZ_KEYS_DIR)
    print("[QZ] CERT:", QZ_CERT_PATH, "| exists:", QZ_CERT_PATH.exists())
    print("[QZ] PRIVATE KEY:", QZ_PRIVATE_KEY_PATH, "| exists:", QZ_PRIVATE_KEY_PATH.exists())
    print("[QZ] PRINTER:", QZ_PRINTER_NAME or "AUTO DETECT")


# ============================================================
# SMS CENTER / BEEM AFRICA
# ============================================================
# Values are read from BASE_DIR/.env or the server environment.

BEEM_API_KEY = os.getenv("BEEM_API_KEY", "").strip()
BEEM_SECRET_KEY = os.getenv("BEEM_SECRET_KEY", "").strip()
BEEM_SENDER_ID = os.getenv("BEEM_SENDER_ID", "").strip().upper()

BEEM_SEND_URL = os.getenv(
    "BEEM_SEND_URL",
    "https://apisms.beem.africa/v1/send",
).strip()

BEEM_BALANCE_URL = os.getenv(
    "BEEM_BALANCE_URL",
    "https://apisms.beem.africa/public/v1/vendors/balance",
).strip()

BEEM_TIMEOUT = env_int("BEEM_TIMEOUT", 20)
BEEM_SMS_BATCH_SIZE = env_int("BEEM_SMS_BATCH_SIZE", 50)
SMS_MAX_RECIPIENTS_PER_SEND = env_int("SMS_MAX_RECIPIENTS_PER_SEND", 500)
BEEM_DLR_TOKEN = os.getenv("BEEM_DLR_TOKEN", "").strip()

# KILASI_V2_PHASE5A_SMS
# All outbound SMS disabled until explicitly enabled and approved for real use.
KILASI_SMS_LIVE_ENABLED = env_bool('KILASI_SMS_LIVE_ENABLED', default=False)
INSTALLED_APPS += ['kilasi_sms']

# KILASI_V2_PHASE5B_AUTOMATION — explicitly OFF by default.
# Separate from KILASI_SMS_LIVE_ENABLED. Never enable against customer data for testing.
KILASI_SMS_AUTOMATION_ENABLED = env_bool('KILASI_SMS_AUTOMATION_ENABLED', default=False)
