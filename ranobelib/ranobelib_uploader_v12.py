"""
Compatibility entrypoint for RanobeLib Uploader.
Main code moved to top-level modules.
"""

from dependencies import check_dependencies

check_dependencies()

from main import main


if __name__ == "__main__":
    main()
