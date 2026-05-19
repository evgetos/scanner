"""
Order Book Density Scanner — Сканер плотностей стакана заявок.

Просто запустите этот файл: python main.py
Зависимости установятся автоматически при первом запуске.
После запуска откройте http://localhost:8000 в браузере.
"""

import subprocess
import sys


def install_dependencies():
    """Install required packages automatically."""
    required = ["fastapi", "uvicorn", "aiohttp", "pydantic"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"Установка зависимостей: {', '.join(missing)}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            stdout=subprocess.DEVNULL,
        )
        print("Зависимости установлены!")


if __name__ == "__main__":
    install_dependencies()

    import uvicorn
    from app.main import app

    print("\n" + "=" * 50)
    print("  Density Scanner запущен!")
    print("  Откройте в браузере: http://localhost:8000")
    print("=" * 50 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)
