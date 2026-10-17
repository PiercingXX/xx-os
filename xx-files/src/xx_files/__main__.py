"""Run the app from a checkout: PYTHONPATH=src python3 -m xx_files."""
import sys


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    from xx_files.main import main as app_main

    raise SystemExit(app_main(args))


if __name__ == "__main__":
    main()
