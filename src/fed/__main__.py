"""Start mit ``python -m fed``: ohne Argumente GUI, mit Argumenten Kommandozeile."""

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from fed.cli import main as cli_main

        return cli_main()
    from fed.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
