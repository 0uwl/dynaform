import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(256 * 1024)))  # 256 KB
    # Host directory of ready-made templates, bind-mounted into the container
    TEMPLATE_DIR = os.environ.get("TEMPLATE_DIR", "")
    # Files in TEMPLATE_DIR larger than this are not listed
    TEMPLATE_MAX_BYTES = int(os.environ.get("TEMPLATE_MAX_BYTES", str(64 * 1024)))  # 64 KB
