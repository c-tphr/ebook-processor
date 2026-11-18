"""The browser tests need Playwright (``pip install -e '.[browser]'`` and ``python -m playwright install``).
Without it they are left out, and the rest of the suite runs as it is."""

import importlib.util

collect_ignore = [] if importlib.util.find_spec("playwright") and importlib.util.find_spec("pytest_playwright") else ["browser"]
