import os
from pathlib import Path
from decouple import config, UndefinedValueError

BASE_DIR = Path(__file__).resolve().parent.parent

# Detect if running on Cloud Run
IS_CLOUD_RUN = os.getenv('K_SERVICE') is not None

# Detect if we're in Docker build (collectstatic phase)
IS_BUILDING = os.getenv('SECRET_KEY') == 'temp-build-key'

# SECRET_KEY - use environment variable in production
if IS_CLOUD_RUN:
    SECRET_KEY = os.getenv('SECRET_KEY')
elif IS_BUILDING:
    SECRET_KEY = 'temp-build-key'
else:
    SECRET_KEY = config('SECRET_KEY')

# DEBUG - always False in production
if IS_CLOUD_RUN or IS_BUILDING:
    DEBUG = False
else:
    DEBUG = config('DEBUG', default=False, cast=bool)

# ALLOWED_HOSTS
if IS_CLOUD_RUN:
    ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')
else:
    ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'crispy_forms',
    'crispy_bootstrap5',
    'storages',  # For Google Cloud Storage
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # For static files
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'csass_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'core' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'csass_project.wsgi.application'

# Database Configuration
if IS_BUILDING:
    # During Docker build, use a dummy database (won't be accessed)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }
elif IS_CLOUD_RUN:
    # Production - Cloud SQL
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': f'/cloudsql/{os.getenv("CLOUD_SQL_CONNECTION_NAME")}',
            'USER': os.getenv('DB_USER'),
            'PASSWORD': os.getenv('DB_PASSWORD'),
            'NAME': os.getenv('DB_NAME'),
        }
    }
else:
    # Local Development
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DATABASE_NAME', default='postgres'),
            'USER': config('DATABASE_USER', default='csassadmin'),
            'PASSWORD': config('DATABASE_PASSWORD'),
            'HOST': config('DATABASE_HOST', default='localhost'),
            'PORT': config('DATABASE_PORT', default='5432'),
        }
    }

# CUSTOM USER MODEL - CRITICAL SETTING
AUTH_USER_MODEL = 'core.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
if IS_BUILDING:
    TIME_ZONE = 'America/New_York'
elif IS_CLOUD_RUN:
    TIME_ZONE = os.getenv('TIMEZONE', 'America/New_York')
else:
    TIME_ZONE = config('TIMEZONE', default='America/New_York')
USE_I18N = True
USE_TZ = True

# Static Files Configuration
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Always include static folder if it exists (needed for collectstatic)
if (BASE_DIR / 'static').exists():
    STATICFILES_DIRS = [BASE_DIR / 'static']

# WhiteNoise configuration for serving static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media Files Configuration
# Media Files Configuration
if IS_BUILDING:
    # During build, don't use GCS at all
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'
elif IS_CLOUD_RUN:
    # Use Google Cloud Storage for media files in production
    DEFAULT_FILE_STORAGE = 'storages.backends.gcs.GSGoogleCloudStorage'
    GS_BUCKET_NAME = os.getenv('GS_BUCKET_NAME', 'csass-474705-media')
    GS_PROJECT_ID = 'csass-474705'
    GS_AUTO_CREATE_BUCKET = False
    GS_DEFAULT_ACL = 'publicRead'
    GS_FILE_OVERWRITE = False
    GS_QUERYSTRING_AUTH = False
    MEDIA_URL = f'https://storage.googleapis.com/{GS_BUCKET_NAME}/'
else:
    # Local development - use local filesystem
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'calendar'
LOGOUT_REDIRECT_URL = 'login'

# Email Configuration
EMAIL_BACKEND = "sendgrid_backend.SendgridBackend"
if IS_BUILDING:
    SENDGRID_API_KEY = 'temp-build-key'
    DEFAULT_FROM_EMAIL = 'temp@example.com'
elif IS_CLOUD_RUN:
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
    DEFAULT_FROM_EMAIL = os.getenv("FROM_EMAIL")
else:
    SENDGRID_API_KEY = config("SENDGRID_API_KEY")
    DEFAULT_FROM_EMAIL = config("FROM_EMAIL")

# Password Reset Settings
PASSWORD_RESET_TIMEOUT = 86400  # 24 hours in seconds

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Session Settings
SESSION_COOKIE_AGE = 28800  # 8 hours
SESSION_SAVE_EVERY_REQUEST = True

# Custom Settings
MAX_LOGIN_ATTEMPTS = 5
EMAIL_TIMEOUT = 5

# Security Settings for Production
if IS_CLOUD_RUN:
    # Cloud Run terminates SSL at the load balancer
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
else:
    # Disable password validators and enable sandbox mode for development
    if not IS_BUILDING:
        AUTH_PASSWORD_VALIDATORS = []
        SENDGRID_SANDBOX_MODE_IN_DEBUG = False