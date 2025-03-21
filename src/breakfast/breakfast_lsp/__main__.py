import sys

from breakfast import __version__
from breakfast.breakfast_lsp import server


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        sys.exit(0)
    server.start()


if __name__ == "__main__":
    main()
