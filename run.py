import logging
from src.safe_logging import RedactingFormatter

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
for handler in logging.getLogger().handlers:
    handler.setFormatter(RedactingFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))


if __name__ == "__main__":
    from src.bot import main

    main()
