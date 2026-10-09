"""
GUI. Ничего не обрабатывает: не читает адреса, не проверяет ключи, не считает статистику.
Передаёт утилите файлы, ключи и одну из заранее записанных команд,
потом показывает готовые файлы утилиты как есть.

Запуск:  pip install streamlit requests
         streamlit run gui.py
(gui.py и utility.py должны лежать в одной папке)
"""
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime

import streamlit as st

HERE = pathlib.Path(__file__).resolve().parent
UTILITY = HERE / "utility.py"
DB_FILE = HERE / "cache.sqlite"
WORK = HERE / "work"
SAVED = HERE / "results"

# ---------- ЗАРАНЕЕ ЗАПИСАННЫЕ КОМАНДЫ (whitelist) ----------
COMMANDS = {
    "Балансы / токены / NFT / транзакции": "balances",
    "Метки и связи (Arkham)": "labels",
}
NETWORKS = ["evm", "btc", "tron", "solana"]
TX_LIMITS = [20, 50, 100, 500, 1000]

def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def read_bytes(path):
    try:
        return pathlib.Path(path).read_bytes()
    except Exception:
        return b""


def stop_job(job):
    if job and job["proc"].poll() is None:
        job["proc"].terminate()
        job["stopped"] = True


st.set_page_config(page_title="Wallet Checker", layout="wide")
st.title("Проверка адресов")

if "job" not in st.session_state:
    st.session_state.job = None
if "gen" not in st.session_state:
    st.session_state.gen = 0

job = st.session_state.job
running = bool(job and job["proc"].poll() is None)
gen = st.session_state.gen

# ---------- ФАЙЛ С КЛЮЧАМИ ----------
with st.sidebar:
    st.header("Файл с ключами")
    st.caption("Текстовый файл, по строке на ключ: ИМЯ=значение. Имена: APIFY_TOKEN, "
               "BLOCKCHAIR_KEY, TRONSCAN_KEY, GMGN_KEY, ARKHAM_KEY, DEBANK_KEY. "
               "Файл передаётся утилите как есть, ключи читает и проверяет она. "
               "Если файл не выбран, утилита ищет keys.txt рядом с собой.")
    keys_up = st.file_uploader("Файл с ключами", type=["txt", "env"], disabled=running)
    if st.button("Очистить кэш"):
        DB_FILE.unlink(missing_ok=True)
        st.success("Кэш очищен")

# ---------- ВВОД ----------
st.subheader("1. Файл с адресами")
up = st.file_uploader("CSV или TXT", type=["csv", "txt"], key=f"file{gen}", disabled=running)
pasted = st.text_area("Или вставьте адреса сюда", height=100, key=f"text{gen}", disabled=running)

st.subheader("2. Параметры")
c1, c2, c3, c4 = st.columns(4)
cmd_title = c1.selectbox("Команда", list(COMMANDS), disabled=running)
network = c2.selectbox("Сеть", NETWORKS, disabled=running)
tx_limit = c3.selectbox("Лимит транзакций", TX_LIMITS, index=2, disabled=running)
use_cache = c4.checkbox("Использовать кэш", value=True, disabled=running)

# ---------- УПРАВЛЕНИЕ ----------
st.subheader("3. Управление")
b1, b2, b3, b4 = st.columns(4)
start = b1.button("▶ СТАРТ", type="primary", use_container_width=True, disabled=running)
stop = b2.button("■ СТОП", use_container_width=True, disabled=not running)
save = b3.button("💾 СОХРАНИТЬ", use_container_width=True)
clear = b4.button("✕ ОЧИСТИТЬ", use_container_width=True)

if clear:
    stop_job(job)
    st.session_state.job = None
    st.session_state.gen += 1
    st.session_state.saved_msg = None
    st.rerun()

if stop:
    stop_job(job)
    st.rerun()

if save:
    if job and pathlib.Path(job["out"]).exists() and pathlib.Path(job["out"] + ".csv").exists():
        SAVED.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jp, cp = SAVED / f"result_{stamp}.json", SAVED / f"result_{stamp}.csv"
        jp.write_bytes(read_bytes(job["out"]))   # файлы утилиты копируются без изменений
        cp.write_bytes(read_bytes(job["out"] + ".csv"))
        st.session_state.saved_msg = f"Сохранено:\n\n{jp}\n\n{cp}"
    else:
        st.session_state.saved_msg = "Пока нечего сохранять: нет результатов."
if st.session_state.get("saved_msg"):
    st.success(st.session_state.saved_msg)

if start:
    inputs = []
    WORK.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if up:
        f = WORK / f"input_{stamp}_file.txt"
        f.write_bytes(up.getvalue())   # файл сохраняется как есть
        inputs.append(str(f))
    if pasted.strip():
        f = WORK / f"input_{stamp}_text.txt"
        f.write_text(pasted, encoding="utf-8")
        inputs.append(str(f))
    if not inputs:
        st.warning("Загрузите файл или вставьте список.")
    else:
        out = str(WORK / f"result_{stamp}.json")
        err = str(WORK / f"error_{stamp}.txt")
        keys_args = []
        if keys_up:
            kf = WORK / f"keys_{stamp}.txt"
            kf.write_bytes(keys_up.getvalue())   # файл ключей сохраняется как есть
            keys_args = ["--keys-file", str(kf)]
        args = [sys.executable, str(UTILITY),
                "--command", COMMANDS[cmd_title], "--network", network,
                "--input", *inputs, "--out", out,
                "--tx-limit", str(tx_limit), "--use-cache", "1" if use_cache else "0",
                *keys_args]
        with open(err, "w", encoding="utf-8") as ef:
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=ef)
        st.session_state.saved_msg = None
        st.session_state.job = {"proc": proc, "out": out, "err": err, "stopped": False}
        st.rerun()

# ---------- ПОКАЗ ФАЙЛОВ УТИЛИТЫ ----------
job = st.session_state.job
if job:
    running = job["proc"].poll() is None
    p = read_json(job["out"] + ".progress", {})

    if p.get("keys_info"):
        st.caption(p["keys_info"])
    if p.get("fatal"):
        st.error(p["fatal"])
    elif not running and job["proc"].returncode not in (0, None) and not job["stopped"]:
        st.error("Утилита завершилась с ошибкой:\n\n" +
                 read_bytes(job["err"]).decode("utf-8", errors="ignore"))
    else:
        st.progress(float(p.get("progress", 0.0)))
        text = p.get("status", "Утилита запускается...")
        if job["stopped"]:
            st.warning(text if text.startswith("Остановлено") else "Остановлено. " + text)
        else:
            st.write(text)

    view = p.get("stats_view", [])
    if view:
        st.subheader("Статистика")
        for i in range(0, len(view), 4):
            cols = st.columns(4)
            for col, (label, value) in zip(cols, view[i:i + 4]):
                col.metric(label, value)

    rows = read_json(job["out"] + ".table.json", [])
    if rows:
        if len(rows) <= 200:
            st.table(rows)  # таблица без обрезания текста в ячейках
        else:
            st.dataframe(rows, use_container_width=True)  # длинный список: прокручиваемая таблица
        full = read_json(job["out"], [])
        idx = st.selectbox("Подробно по адресу", range(len(rows)),
                           format_func=lambda i: rows[i]["address"])
        if idx < len(full):
            st.json(full[idx], expanded=True)
        d1, d2 = st.columns(2)
        d1.download_button("Скачать JSON", read_bytes(job["out"]), "result.json", "application/json")
        d2.download_button("Скачать CSV", read_bytes(job["out"] + ".csv"), "result.csv", "text/csv")

    if running:
        time.sleep(1)
        st.rerun()
