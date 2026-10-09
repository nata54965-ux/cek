#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Show installation progress when opened through the file manager.
if [[ "${1:-}" != "--install" ]]; then
    exec x-terminal-emulator -e bash "$project_dir/Setup.sh" --install
fi
trap 'printf "\nНажмите Enter, чтобы закрыть окно.\n"; read -r _' EXIT
cd "$project_dir"
if ! python3 -c 'import tkinter, venv, ensurepip' >/dev/null 2>&1; then
    printf 'Установка компонентов Python для Ubuntu. Может потребоваться пароль.\n'
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-tk
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
from pathlib import Path
import os

here = Path.cwd()
# Escape according to the Desktop Entry specification, not shell quoting.
def quote(value):
    value = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'

entry = ('[Desktop Entry]\nType=Application\nName=Wallet Checker\n'
         'Comment=Проверка адресов\nTerminal=false\nIcon=utilities-terminal\n'
         'Exec=' + quote(here / '.venv/bin/python') + ' ' + quote(here / 'Start.py') + '\n')
launcher = here / 'Wallet Checker.desktop'
launcher.write_text(entry, encoding='utf-8')
launcher.chmod(0o755)
apps = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
apps.mkdir(parents=True, exist_ok=True)
(apps / 'wallet-checker.desktop').write_text(entry, encoding='utf-8')
print('Готово! Откройте Wallet Checker в меню приложений Ubuntu.')
print('Также создан файл Wallet Checker.desktop в папке проекта.')
PY
