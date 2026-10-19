"""Run in PyCharm to check the CSVs before importing. No database connection."""
from main import main

if __name__ == "__main__":
    main(["--dry-run"])
