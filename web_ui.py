"""Lance l'interface web locale job-bot sur http://127.0.0.1:5000."""
import os
import sys

# S'assurer que la racine du projet est dans sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.src.web.app import app

if __name__ == "__main__":
    print()
    print("  job-bot UI  →  http://127.0.0.1:5000")
    print("  Ctrl+C pour arrêter.")
    print()
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
