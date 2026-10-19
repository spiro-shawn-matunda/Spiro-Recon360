"""Run in PyCharm to import only the supplied Rwanda exports."""
import sys
from main import main

if __name__ == "__main__":
    try:
        main(["--file-prefix", "Rwanda_"])
    except Exception as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        sys.exit(1)
