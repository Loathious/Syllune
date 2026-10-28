#!/usr/bin/env python3
# start-syllune.py
# Development launcher: runs Syllune straight from this source checkout,
# without installing it. Released builds start from packaging/windows instead.

if __name__ == "__main__":
    from src.syllune.main import main
    main()
