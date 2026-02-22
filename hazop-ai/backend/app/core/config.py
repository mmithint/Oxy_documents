from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "HAZOP AI Assistant"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"
    AZURE_OPENAI_DEPLOYMENT: str = ""  # GPT model deployment name
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = ""  # Embedding model deployment name

    # Azure Document Intelligence
    AZURE_DOC_INTELLIGENCE_ENDPOINT: str = ""
    AZURE_DOC_INTELLIGENCE_KEY: str = ""

    # Azure Blob Storage
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER: str = "pid-uploads"

    # MongoDB (local or Azure Cosmos DB MongoDB vCore)
    COSMOS_CONNECTION_STRING: str = "mongodb://localhost:27017/"
    COSMOS_DATABASE_NAME: str = "Oxy"

    # Embedding rate-limit tuning (Azure OpenAI TPM quota for the embedding deployment)
    EMBEDDING_TPM_LIMIT: int = 240000

    # ODA File Converter — DWG → PDF conversion
    # Download from: https://www.opendesign.com/guestfiles/oda_file_converter
    # Example (Windows): C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe
    # Example (Linux):   /usr/bin/ODAFileConverter
    ODA_CONVERTER_PATH: str = ""

    # Anthropic Claude API (for GPT vs Claude comparison feature)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
