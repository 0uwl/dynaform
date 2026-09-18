import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(256 * 1024)))  # 256 KB
    # Host directory of ready-made templates, bind-mounted into the container.
    # Empty (the default outside the container) turns the picker off entirely.
    TEMPLATE_DIR = os.environ.get("TEMPLATE_DIR", "")
    # Files in TEMPLATE_DIR larger than this are not listed. Kept well under
    # MAX_CONTENT_LENGTH because a chosen template travels back to the server
    # inside the textarea of the next request.
    TEMPLATE_MAX_BYTES = int(os.environ.get("TEMPLATE_MAX_BYTES", str(64 * 1024)))  # 64 KB
