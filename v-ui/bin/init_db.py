import os
import sys

# Add the project root to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.database import init_db

if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Database initialized at data/v-ui.db")
