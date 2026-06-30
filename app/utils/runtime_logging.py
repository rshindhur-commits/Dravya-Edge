from app.config.settings import settings


def debug_print(*args, **kwargs):

    if settings.scanner_debug:

        print(*args, **kwargs)
