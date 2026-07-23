from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-nano-12b-v2-vl:free"

    google_sheet_id: str = ""
    google_sheet_tab: str = "Training Data"
    google_drive_folder_id: str = ""
    google_drive_listing_images_folder_id: str = ""

    database_url: str = "sqlite:///./data/app.db"
    data_dir: str = "./data"

    public_webhook_base_url: str = ""

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
