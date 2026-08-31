"""包入口：python -m src <session_id>"""
import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())