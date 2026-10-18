import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "Newtonite Operations Under Pressure"
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./newtonite_ops.db")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-newtonite-key-change-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    OUTBOX_POLL_INTERVAL_SECONDS: float = float(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "2.0"))

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
