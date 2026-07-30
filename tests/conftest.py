"""Pytest entry point.

Importing the package runs tests/__init__.py, which redirects DRAVYA_DATA_DIR and
DRAVYA_STATE_DIR to a throwaway sandbox before any app module is imported. Keep
this import: it is what stops a pytest run from writing into the real trading
artifacts under data/ and app/state/.
"""

import tests  # noqa: F401
