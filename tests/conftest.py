import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "filestorage"
SALT = "sel_de_test_uniquement_ne_pas_reutiliser_en_production"
