"""Run in PyCharm to load only the Wallets export and its customer mapping."""
import sys
from main import main

if __name__ == '__main__':
    try:
        main(['--wallet-master-only'])
    except Exception as exc:
        print(f'Wallet import failed: {exc}', file=sys.stderr)
        sys.exit(1)
