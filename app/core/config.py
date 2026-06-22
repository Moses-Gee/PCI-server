from pydantic_settings import BaseSettings 
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE: int = 10_485_760

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra="ignore"


settings = Settings()
