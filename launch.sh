#!/usr/bin/env sh
# AR-Инспектор для macOS и Linux:  ./launch.sh [setup|run|check] [параметры]
cd "$(dirname "$0")" || exit 1
for py in python3.12 python3 python; do
  # Проверяем, что интерпретатор реально запускается и он 3.10+ (на Windows python3 бывает заглушкой Store).
  if command -v "$py" >/dev/null 2>&1 && \
     "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    exec "$py" start.py "$@"
  fi
done
echo "Не найден Python 3.10+. Установите его (https://www.python.org/downloads/) и повторите." >&2
exit 1
