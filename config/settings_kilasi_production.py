"""KILASI V2 production override: guarded, never uses rehearsal DB, SMS OFF by default."""
from .settings import *  # noqa
from pathlib import Path as _Path
import os as _os
import sqlite3 as _sqlite3
from contextlib import closing as _closing

if _os.getenv('DJANGO_DEBUG','1').strip().lower() not in ('0','false','no','off'):
    raise RuntimeError('Production requires DJANGO_DEBUG=0')
if len(_os.getenv('DJANGO_SECRET_KEY',''))<40:
    raise RuntimeError('Set unique DJANGO_SECRET_KEY (at least 40 characters) in protected server environment')
_path_raw=_os.getenv('KILASI_PRODUCTION_DB','').strip()
if not _path_raw or not _Path(_path_raw).is_absolute():
    raise RuntimeError('Set KILASI_PRODUCTION_DB to the ABSOLUTE path of the final activated SQLite database')
_db=_Path(_path_raw).resolve()
if not _db.is_file() or 'REHEARSAL' in _db.name.upper() or 'STAGE7' in _db.name.upper():
    raise RuntimeError('Production database absent or is an unsafe rehearsal/staging database')
with _closing(_sqlite3.connect(_db.as_uri()+'?mode=ro',uri=True)) as _c:
    _m=_c.execute('SELECT phase,legacy_receipts_locked,sms_disabled,production_approval FROM kilasi_stage8_manifest WHERE id=1').fetchone()
    _approval=_c.execute('SELECT phase,approved FROM kilasi_stage9_cutover WHERE id=1').fetchone()
    if tuple(_m or ())!=('9C.1',1,1,1) or tuple(_approval or ())!=('9C.1',1):
        raise RuntimeError('Production candidate not explicitly activated or historic protections missing')
    _triggers=_c.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'kilasi8_%'").fetchone()[0]
    _frozen=_c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND (name LIKE 'kilasi_legacy_%' OR name LIKE 'kilasi_stage7g_%' OR name LIKE 'kilasi_stage7h_%')").fetchone()[0]
    if _triggers != 7 + 3*(_frozen+1):raise RuntimeError('Historical immutability triggers missing')
DATABASES=dict(DATABASES)
DATABASES['default']={**DATABASES['default'],'NAME':str(_db),'OPTIONS':{'timeout':30}}
ROOT_URLCONF='config.urls_kilasi_production'
INSTALLED_APPS=list(INSTALLED_APPS)
if 'kilasi_legacy_viewer' not in INSTALLED_APPS:INSTALLED_APPS.append('kilasi_legacy_viewer')
MIDDLEWARE=['kilasi_launch_production.middleware.GuardedOperations']+list(MIDDLEWARE)
TEMPLATES=list(TEMPLATES);TEMPLATES[0]=dict(TEMPLATES[0]);TEMPLATES[0]['OPTIONS']=dict(TEMPLATES[0]['OPTIONS'])
_cp=list(TEMPLATES[0]['OPTIONS']['context_processors'])
if 'kilasi_legacy_viewer.context_processors.staging_nav' not in _cp:_cp.append('kilasi_legacy_viewer.context_processors.staging_nav')
TEMPLATES[0]['OPTIONS']['context_processors']=_cp
DEBUG=False
ALLOWED_HOSTS=[h.strip() for h in _os.getenv('KILASI_ALLOWED_HOSTS','kilasiinvestment.store,www.kilasiinvestment.store').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS=['https://'+h for h in ALLOWED_HOSTS if h not in ('localhost','127.0.0.1')]
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
SECURE_SSL_REDIRECT=True
USE_THOUSAND_SEPARATOR=True
# ==========================================
# BEEM SMS PRODUCTION CONFIGURATION
# ==========================================

KILASI_SMS_LIVE_ENABLED = env_bool(
    "KILASI_SMS_LIVE_ENABLED",
    default=False,
)

KILASI_SMS_AUTOMATION_ENABLED = env_bool(
    "KILASI_SMS_AUTOMATION_ENABLED",
    default=False,
)

BEEM_API_KEY = os.getenv("BEEM_API_KEY", "").strip()
BEEM_SECRET_KEY = os.getenv("BEEM_SECRET_KEY", "").strip()
BEEM_SENDER_ID = os.getenv("BEEM_SENDER_ID", "KILASI LTD").strip()
BEEM_DLR_TOKEN=''
BEEM_SEND_URL = _os.getenv(
    "BEEM_SEND_URL",
    "https://apisms.beem.africa/v1/send",
).strip()

BEEM_BALANCE_URL = _os.getenv(
    "BEEM_BALANCE_URL",
    "https://apisms.beem.africa/public/v1/vendors/balance",
).strip()
EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'
