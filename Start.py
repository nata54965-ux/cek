"""Double-click launcher; requires Python with Tk and the local .venv."""
import os
from pathlib import Path
import socket
import subprocess
import time
import tkinter as tk
from tkinter import messagebox
from urllib.request import urlopen
import webbrowser

HERE = Path(__file__).resolve().parent


def main():
    root = tk.Tk()
    root.title('Wallet Checker')
    root.geometry('480x140')
    status = tk.StringVar(value='Запуск интерфейса…')
    tk.Label(root, textvariable=status, wraplength=460).pack(pady=15)
    python = HERE / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        messagebox.showerror('Нужна установка', 'Сначала запустите Setup.sh в папке проекта.', parent=root)
        root.destroy()
        return
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    try:
        log = (HERE / 'launcher.log').open('a', encoding='utf-8')
        try:
            proc = subprocess.Popen(
                [str(python), '-m', 'streamlit', 'run', str(HERE / 'gui.py'),
                 '--server.address=127.0.0.1', f'--server.port={port}',
                 '--server.headless=true', '--browser.gatherUsageStats=false'],
                cwd=HERE, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True)
        finally:
            log.close()
    except OSError as exc:
        messagebox.showerror('Ошибка запуска', str(exc), parent=root)
        root.destroy()
        return

    def close():
        if proc.poll() is None:
            import signal
            os.killpg(proc.pid, signal.SIGTERM)
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    tk.Button(root, text='Закрыть программу', command=close).pack()
    deadline = time.monotonic() + 60

    def ready():
        if proc.poll() is not None or time.monotonic() > deadline:
            messagebox.showerror('Ошибка запуска',
                                 'Интерфейс не запустился. Подробности в launcher.log.', parent=root)
            close()
            return
        try:
            with urlopen(url + '/_stcore/health', timeout=0.3) as response:
                healthy = response.status == 200
        except OSError:
            healthy = False
        if healthy:
            status.set('Интерфейс: ' + url + '\nДля завершения нажмите «Закрыть программу».')
            tk.Button(root, text='Открыть интерфейс', command=lambda: webbrowser.open(url)).pack()
            webbrowser.open(url)
        else:
            root.after(300, ready)

    root.after(300, ready)
    root.mainloop()


if __name__ == '__main__':
    main()
