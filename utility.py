"""
utility.py - утилита. Получает ФАЙЛ с адресами, сама его читает, вызывает сервисы из ТЗ,
пишет результат и статистику в файлы. GUI только запускает её и показывает файлы.

Пример запуска без GUI:
  python utility.py --command balances --network evm --input addrs.txt --out result.json
Результат: result.json  (список ответов)
Статистика: result.json.progress  (обновляется во время работы)
Ключи API читаются из файла .env рядом с утилитой.
"""
import argparse
import csv
import json
import logging
import os
import pathlib
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = pathlib.Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
DB_FILE = HERE / "cache.sqlite"
LOG_FILE = HERE / "requests.log"
WORKERS = 4  # параллельных запросов
APIFY_MAX_WAIT = 900  # секунд на один запуск актора

logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


def load_env(path=None):
    """Читает ключи из файла со строками ИМЯ=значение.
    Без --keys-file берёт keys.txt или .env рядом с утилитой. Возвращает (ключи, путь к файлу)."""
    if path is None:
        path = next((x for x in (HERE / "keys.txt", ENV_FILE) if x.exists()), None)
    data = {}
    if path and pathlib.Path(path).exists():
        for line in pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    return data, path


# ---------- КЭШ SQLite ----------
_db_lock = threading.Lock()


def _db():
    c = sqlite3.connect(str(DB_FILE))
    c.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
    return c


def cache_get(key):
    with _db_lock:
        c = _db()
        try:
            r = c.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
        finally:
            c.close()
    return json.loads(r[0]) if r else None


def cache_put(key, value):
    with _db_lock:
        c = _db()
        try:
            c.execute("REPLACE INTO cache (k, v) VALUES (?, ?)", (key, json.dumps(value)))
            c.commit()
        finally:
            c.close()


# ---------- ЗАПРОСЫ К СЕРВИСАМ ИЗ ТЗ ----------
# CHECK = сверьте с документацией сервиса.

def q_evm_apify(addr, k, tx_limit):
    base = "https://api.apify.com/v2"
    p = {"token": k["APIFY_TOKEN"]}
    body = {"addresses": [addr], "maxTransactions": tx_limit}  # CHECK: имена полей на странице актора
    r = requests.post(f"{base}/acts/yadroo~wallet-intel/runs", params=p, json=body, timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    deadline = time.time() + APIFY_MAX_WAIT
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            try:
                requests.post(f"{base}/actor-runs/{run['id']}/abort", params=p, timeout=30)
            except Exception:
                pass
            raise TimeoutError(f"Apify не успел за {APIFY_MAX_WAIT} с, запуск отменён")
        time.sleep(3)
        r = requests.get(f"{base}/actor-runs/{run['id']}", params=p, timeout=60)
        r.raise_for_status()
        run = r.json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify: запуск завершился со статусом {run['status']}")
    r = requests.get(f"{base}/datasets/{run['defaultDatasetId']}/items",
                     params={**p, "format": "json"}, timeout=120)
    r.raise_for_status()
    return r.json()


def q_btc_blockchair(addr, k, tx_limit):
    url = f"https://api.blockchair.com/bitcoin/dashboards/address/{addr}"
    params = {"limit": tx_limit}
    if k.get("BLOCKCHAIR_KEY"):
        params["key"] = k["BLOCKCHAIR_KEY"]
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def q_tron_tronscan(addr, k, tx_limit):
    url = "https://apilist.tronscanapi.com/api/accountv2"
    headers = {"TRON-PRO-API-KEY": k["TRONSCAN_KEY"]} if k.get("TRONSCAN_KEY") else {}
    r = requests.get(url, params={"address": addr}, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def q_sol_gmgn(addr, k, tx_limit):
    url = f"https://gmgn.ai/api/v1/wallet_holdings/sol/{addr}"  # CHECK: эндпоинт по документации GMGN
    r = requests.get(url, headers={"Authorization": f"Bearer {k['GMGN_KEY']}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def q_arkham(addr, k, tx_limit):
    url = f"https://api.arkm.com/intelligence/address/{addr}"  # CHECK: эндпоинт по документации Arkham
    r = requests.get(url, headers={"API-Key": k["ARKHAM_KEY"]}, timeout=60)
    r.raise_for_status()
    return r.json()


COMMANDS = {
    "balances": {
        "evm": ("APIFY_TOKEN", q_evm_apify),
        "btc": ("BLOCKCHAIR_KEY", q_btc_blockchair),
        "tron": ("TRONSCAN_KEY", q_tron_tronscan),
        "solana": ("GMGN_KEY", q_sol_gmgn),
    },
    "labels": {n: ("ARKHAM_KEY", q_arkham) for n in ("evm", "btc", "tron", "solana")},
}
OPTIONAL_KEYS = {"BLOCKCHAIR_KEY", "TRONSCAN_KEY"}
KEY_NAMES = ("APIFY_TOKEN", "BLOCKCHAIR_KEY", "TRONSCAN_KEY", "GMGN_KEY", "ARKHAM_KEY", "DEBANK_KEY")

# ---------- В КАКОМ ПОЛЕ ОТВЕТА СЕРВИСА БАЛАНС И ТРАНЗАКЦИИ ----------
# "*" = первый ключ словаря, число = номер элемента списка.
# CHECK: для evm и solana впишите пути после первого реального ответа сервиса.
# Пример для Apify: {"balance": "0.nativeBalance", "tx": "0.txCount"}
FIELD_MAP = {
    "btc": {"balance": "data.*.address.balance", "tx": "data.*.address.transaction_count"},
    "tron": {"balance": "balance", "tx": "totalTransactionCount"},
    "evm": {"balance": None, "tx": None},
    "solana": {"balance": None, "tx": None},
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(data, path):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = next(iter(cur.values()), None) if part == "*" else cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def analyze(net, data):
    m = FIELD_MAP.get(net, {})
    bal = _num(_get(data, m["balance"])) if m.get("balance") else None
    tx = _num(_get(data, m["tx"])) if m.get("tx") else None
    return (bal > 0 if bal is not None else None), (tx > 0 if tx is not None else None)


# ---------- СТАТИСТИКА ----------
def make_stats(results, loaded, running, fatal=None):
    """Вся статистика считается здесь. GUI только показывает готовые поля
    progress / status / stats_view."""
    ok = [r for r in results if "error" not in r]
    done = len(results)
    n_ok, n_err = len(ok), done - len(ok)
    with_tx = sum(1 for r in ok if r.get("has_tx") is True)
    with_bal = sum(1 for r in ok if r.get("has_balance") is True)
    no_bal = sum(1 for r in ok if r.get("has_balance") is False)
    no_data = sum(1 for r in ok if r.get("has_balance") is None)
    return {
        "running": running,
        "fatal": fatal,
        "progress": min(done / loaded, 1.0) if loaded else 0.0,
        "status": (f"Обработано {done} из {loaded}" if running
                   else f"Готово. Обработано {done} из {loaded}"),
        "stats_view": [
            ["Загружено ключей", loaded], ["Проверено", n_ok], ["Ошибок", n_err],
            ["Осталось", max(loaded - done, 0)],
            ["С транзакциями", with_tx], ["С балансом", with_bal],
            ["Без баланса", no_bal], ["Нет данных от сервиса", no_data],
        ],
    }


def table_rows(results):
    yn = {True: "да", False: "нет", None: "нет данных"}
    return [
        {
            "address": r.get("address"),
            "network": r.get("network"),
            "status": ("ошибка: " + r["error"]) if "error" in r else "ok",
            "баланс": yn.get(r.get("has_balance"), ""),
            "транзакции": yn.get(r.get("has_tx"), ""),
            "из кэша": r.get("cached", ""),
        }
        for r in results
    ]


def write_json(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_outputs(out, results):
    """Утилита сама пишет все готовые файлы: полный JSON, таблицу JSON и CSV."""
    rows = table_rows(results)
    write_json(out, results)
    write_json(out + ".table.json", rows)
    tmp = out + ".csv.tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["address", "network", "status", "баланс",
                                          "транзакции", "из кэша", "ответ сервиса (полный)"])
        w.writeheader()
        for row, r in zip(rows, results):
            full = json.dumps(r["data"], ensure_ascii=False) if "data" in r else ""
            w.writerow({**row, "ответ сервиса (полный)": full})
    os.replace(tmp, out + ".csv")


def check_key(key_name, keys):
    """Проверка ключа самой утилитой. Возвращает текст ошибки или None.
    Apify проверяется заранее. Для остальных сервисов ответ 401/403 по адресу записывается
    как ошибка этого адреса, и утилита берёт следующий адрес."""
    val = keys.get(key_name, "")
    if not val:
        return None if key_name in OPTIONAL_KEYS else f"В файле ключей нет {key_name}"
    if key_name == "APIFY_TOKEN":
        try:
            r = requests.get("https://api.apify.com/v2/users/me", params={"token": val}, timeout=30)
        except Exception as e:
            return f"Не удалось проверить ключ {key_name}: {e}"
        if r.status_code in (401, 403):
            return f"Ключ {key_name} недействителен (Apify ответил {r.status_code})"
    return None


def build_parser():
    """CLI contract, also used by compatibility checks."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--command", required=True, choices=list(COMMANDS))
    ap.add_argument("--network", required=True, choices=["evm", "btc", "tron", "solana"])
    ap.add_argument("--input", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tx-limit", type=int, default=100, choices=[20, 50, 100, 500, 1000])
    ap.add_argument("--use-cache", type=int, default=1, choices=[0, 1])
    ap.add_argument("--keys-file", default=None, help="файл с ключами (ИМЯ=значение)")
    return ap


def main():
    args = build_parser().parse_args()

    # Ключи берутся только из файла с ключами
    keys, keys_path = load_env(args.keys_file)
    found = [k for k in KEY_NAMES if keys.get(k)]
    keys_info = (f"Файл ключей: {keys_path or 'не найден'}. "
                 f"Найдены ключи: {', '.join(f'{k}={keys[k]}' for k in found) or 'нет'}")
    text = "\n".join(pathlib.Path(x).read_text(encoding="utf-8", errors="ignore") for x in args.input)
    addrs = list(dict.fromkeys(
        l.strip().split(",")[0].strip() for l in text.splitlines() if l.strip()))
    loaded = len(addrs)
    key_name, func = COMMANDS[args.command][args.network]

    results, fatal, lock, last = [], [], threading.Lock(), [0.0]
    stop = threading.Event()
    progress_path = args.out + ".progress"

    def flush(force=False, running=True):
        now = time.time()
        if not force and now - last[0] < 1:
            return
        last[0] = now
        write_outputs(args.out, results)
        st = make_stats(results, loaded, running, fatal[0] if fatal else None)
        st["keys_info"] = keys_info
        if stop.is_set():
            st["status"] = f"Остановлено. Обработано {len(results)} из {loaded}"
        write_json(progress_path, st)

    err = check_key(key_name, keys)
    if err:
        fatal.append(err)
        flush(force=True, running=False)
        return
    flush(force=True)

    def work(addr):
        if fatal or stop.is_set():
            return
        ck = f"{args.command}|{args.network}|{args.tx_limit}|{addr}"
        try:
            data = cache_get(ck) if args.use_cache else None
            cached = data is not None
            if data is None:
                logging.info("REQUEST %s %s %s", args.command, args.network, addr)
                data = func(addr, keys, args.tx_limit)
                cache_put(ck, data)
            hb, ht = analyze(args.network, data) if args.command == "balances" else (None, None)
            res = {"address": addr, "network": args.network, "cached": cached,
                   "has_balance": hb, "has_tx": ht, "data": data}
        except Exception as e:
            logging.error("ERROR %s %s: %s", args.network, addr, e)
            res = {"address": addr, "network": args.network, "error": str(e)}
        with lock:
            results.append(res)
            flush()

    def on_stop(signum, frame):
        """Ctrl+C или СТОП из GUI: записать готовое и сразу выйти, не дорабатывая список."""
        stop.set()
        with lock:
            flush(force=True, running=False)
        os._exit(0)

    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGTERM, on_stop)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(work, addrs))
    with lock:
        flush(force=True, running=False)


if __name__ == "__main__":
    main()
