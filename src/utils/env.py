import logging

from dotenv import load_dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    BOT_TOKEN: str
    LOCAL_BOT_API_URL: str
    BOT_API_DIR: str
    DOWNLOAD_TO_DIR: str
    USER_ID: str
    CHAT_ID: str
    DOWNLOAD_CONCURRENCY: int = Field(default=2, ge=1, le=10)
    QUEUE_CAPACITY: int = Field(default=1000, ge=1, le=10000)
    TASK_DB_PATH: str = './downloads/.telegram-downloader/tasks.sqlite3'
    DOWNLOAD_TASK_TIMEOUT: float = Field(default=3600, gt=0)
    NOTIFY_BATCH_SECONDS: float = Field(default=5, ge=1)
    NOTIFY_MIN_INTERVAL: float = Field(default=3.1, ge=1)


logger.info("Loading environment variables")

try:
    env = Settings()
except ValidationError as e:
    logger.error("Environment variables validation error: %s", e)
    exit(1)
