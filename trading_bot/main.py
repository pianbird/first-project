"""
DEPRECATED / COMPATIBILITY SHIM
Redirects imports to legacy.main.
"""
from legacy.main import *  # noqa: F401, F403

if __name__ == "__main__":
    import legacy.main as _legacy_main
    _legacy_main.main()
