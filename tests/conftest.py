"""Configuración de pytest: los tests no leen el .env del usuario."""
import os
import sys

os.environ["AUTOTRADER_SKIP_DOTENV"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
