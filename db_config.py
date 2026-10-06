"""Read PostgreSQL connection details from this project's .env file."""
from pathlib import Path

PROJECT = Path(__file__).resolve().parent


def read_database_config(env_path=None):
    path = Path(env_path) if env_path is not None else PROJECT / ".env"
    if not path.is_file():
        raise ValueError("Missing .env file. Copy .env.example to .env and enter your PostgreSQL details.")
    try:
        from dotenv import dotenv_values
    except ImportError:
        raise ValueError("Missing python-dotenv. Install requirements.txt using the project interpreter.") from None
    # Use an explicit path. Passwords containing ${...} remain literal.
    values = dotenv_values(path, encoding="utf-8-sig", interpolate=False)
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required if values.get(key) in (None, "")]
    if missing:
        raise ValueError("Fill in these settings in .env: " + ", ".join(missing))
    try:
        port = int(values["DB_PORT"])
    except ValueError:
        raise ValueError("DB_PORT in .env must be a whole number.") from None
    if not 1 <= port <= 65535:
        raise ValueError("DB_PORT in .env must be between 1 and 65535.")
    return {"host": values["DB_HOST"], "port": port, "dbname": values["DB_NAME"],
            "user": values["DB_USER"], "password": values["DB_PASSWORD"]}
