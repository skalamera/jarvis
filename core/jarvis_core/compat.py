"""Cross-platform helpers (macOS + Windows)."""
import sys


def wf(fmt: str) -> str:
    """strftime format with glibc/BSD no-pad flags (%-d) translated to the Windows form (%#d)."""
    return fmt.replace("%-", "%#") if sys.platform == "win32" else fmt
