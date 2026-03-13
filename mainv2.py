import requests
import time
import threading
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

# ─────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────
API_KEY         = "API_KEY_KAMU"
BASE_URL        = "https://hero-sms.com/stubs/handler_api.php"
SERVICE         = "wa"
COUNTRY         = 54
JUMLAH_NOMOR    = 5
MIN_PRICE       = 0.20
MAX_PRICE       = 0.30
THREADS         = 5
ORDER_DELAY     = 0.3
OTP_CHECK_DELAY = 5

BOT_TOKEN = "TOKEN_BOT_KAMU"
CHAT_ID   = "CHAT_ID_KAMU"

# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────
success  = 0
retry    = 0
cancel   = 0
balance  = "..."
numbers  = []
log_msgs = []

lock    = threading.Lock()
console = Console()

# ─────────────────────────────────────────
#  UTILS
# ─────────────────────────────────────────

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def add_log(msg: str, level: str = "INFO"):
    with lock:
        log_msgs.append((ts(), level, msg))
        if len(log_msgs) > 200:
            log_msgs.pop(0)

def send_telegram(text: str):
    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={"chat_id": CHAT_ID, "text": text},
            timeout=8,
        )
    except Exception:
        pass

# ─────────────────────────────────────────
#  SERVICES
# ─────────────────────────────────────────

def svc_balance():
    global balance
    while True:
        try:
            r = requests.get(
                BASE_URL,
                params={"api_key": API_KEY, "action": "getBalance"},
                timeout=8,
            )
            raw = r.text.strip()
            balance = raw.split(":")[-1] if ":" in raw else raw
        except Exception:
            balance = "ERROR"
        time.sleep(5)

def order_number():
    r = requests.get(
        BASE_URL,
        params={
            "api_key": API_KEY,
            "action":  "getNumber",
            "service": SERVICE,
            "country": COUNTRY,
        },
        timeout=10,
    )
    return r.text

def cancel_number(order_id: str):
    try:
        requests.get(
            BASE_URL,
            params={"api_key": API_KEY, "action": "setStatus",
                    "id": order_id, "status": 8},
            timeout=8,
        )
    except Exception:
        pass

def check_sms(order_id: str, row: dict):
    while True:
        try:
            r = requests.get(
                BASE_URL,
                params={"api_key": API_KEY, "action": "getStatus", "id": order_id},
                timeout=10,
            )
            result = r.text
            if "STATUS_OK" in result:
                otp = result.split(":")[1]
                with lock:
                    row["otp"]    = otp
                    row["status"] = "RECV"
                add_log(f"OTP {otp} [{row['number']}]", "OK")
                send_telegram(f"OTP MASUK\nOrderID : {order_id}\nOTP     : {otp}")
                return
            if "STATUS_CANCEL" in result:
                with lock:
                    row["status"] = "CNCL"
                add_log(f"Cancelled {order_id}", "WARN")
                return
        except Exception as e:
            add_log(f"SMS check err: {e}", "ERR")
        time.sleep(OTP_CHECK_DELAY)

# ─────────────────────────────────────────
#  WORKER
# ─────────────────────────────────────────

def worker():
    global success, retry, cancel

    while True:
        with lock:
            if success >= JUMLAH_NOMOR:
                return
        try:
            result = order_number()
        except Exception as e:
            add_log(f"Request err: {e}", "ERR")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        if "ACCESS_NUMBER" not in result:
            with lock:
                retry += 1
            add_log(f"Order fail: {result}", "WARN")
            time.sleep(ORDER_DELAY)
            continue

        parts    = result.split(":")
        order_id = parts[1]
        number   = parts[2]
        price    = 0.0
        try:
            price = float(parts[3])
        except Exception:
            pass

        if price != 0 and (price < MIN_PRICE or price > MAX_PRICE):
            cancel_number(order_id)
            with lock:
                cancel += 1
            add_log(f"Price {price:.2f} out of range", "WARN")
            continue

        row = {
            "ts":       ts(),
            "number":   number,
            "price":    price,
            "order_id": order_id,
            "otp":      "-",
            "status":   "WAIT",
        }

        with lock:
            success += 1
            numbers.append(row)

        add_log(f"{number}  {price:.2f}  {order_id}", "OK")
        send_telegram(
            f"NOMOR DIDAPAT\nNomor   : {number}\nHarga   : {price}\nOrderID : {order_id}"
        )

        t = threading.Thread(target=check_sms, args=(order_id, row), daemon=True)
        t.start()
        time.sleep(ORDER_DELAY)

# ─────────────────────────────────────────
#  DASHBOARD  (compact, Termux-friendly)
# ─────────────────────────────────────────

LEVEL_COLOR = {
    "OK":   "bright_green",
    "WARN": "yellow",
    "ERR":  "red",
    "INFO": "bright_black",
}

STATUS_COLOR = {
    "RECV": "bright_green",
    "CNCL": "red",
    "WAIT": "yellow",
}

def _divider() -> Text:
    t = Text()
    t.append("─" * 40, style="bright_black")
    return t

def _label(text: str) -> Text:
    t = Text()
    t.append(f" {text} ", style="bold black on bright_black")
    return t

def build() -> Table:
    root = Table(box=None, show_header=False, padding=0, expand=True)
    root.add_column("content", ratio=1)

    now = datetime.now().strftime("%H:%M:%S")

    # ── Title ─────────────────────────────────────────────────────────────
    title = Text(justify="center")
    title.append("  HERO-SMS BOT  ", style="bold black on bright_green")
    title.append(f"  {now}", style="bright_black")
    root.add_row(title)
    root.add_row(Text(""))

    # ── Stats: vertical 2-col, fits any width ─────────────────────────────
    done_pct = (success / JUMLAH_NOMOR * 100) if JUMLAH_NOMOR else 0
    filled   = int(done_pct / 5)   # out of 20

    bar = Text()
    bar.append(" [", style="bright_black")
    bar.append("=" * filled,        style="bold bright_green")
    bar.append("-" * (20 - filled), style="bright_black")
    bar.append("] ", style="bright_black")
    bar.append(f"{done_pct:.0f}%",  style="bold bright_white")

    stat = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    stat.add_column("key", style="bright_black", width=10)
    stat.add_column("val", ratio=1)

    stat.add_row(Text("Balance"),  Text(str(balance), style="bold gold1"))
    stat.add_row(Text("Target"),   Text(str(JUMLAH_NOMOR), style="bright_white"))
    stat.add_row(Text("Success"),  Text(str(success), style="bold bright_green"))
    stat.add_row(Text("Retry"),    Text(str(retry),   style="yellow"))
    stat.add_row(Text("Cancel"),   Text(str(cancel),  style="red"))
    stat.add_row(Text("Progress"), bar)

    root.add_row(Panel(stat, border_style="bright_black", padding=(0, 0)))

    # ── Numbers ───────────────────────────────────────────────────────────
    num_tbl = Table(
        box=box.SIMPLE,
        header_style="bright_black",
        expand=True,
        padding=(0, 1),
    )
    num_tbl.add_column("#",      justify="center", width=3,  style="bright_black")
    num_tbl.add_column("NOMOR",  justify="left",   min_width=14, style="bright_white")
    num_tbl.add_column("HARGA",  justify="right",  width=6,  style="yellow")
    num_tbl.add_column("OTP",    justify="center", width=8,  style="bold bright_green")
    num_tbl.add_column("STATUS", justify="center", width=6)

    with lock:
        rows_snap = list(numbers)

    if rows_snap:
        for i, r in enumerate(rows_snap, 1):
            num_tbl.add_row(
                Text(str(i)),
                Text(r["number"]),
                Text(f"{r['price']:.2f}"),
                Text(r["otp"]),
                Text(r["status"], style=STATUS_COLOR.get(r["status"], "bright_black")),
            )
    else:
        num_tbl.add_row(
            Text("-"), Text("menunggu nomor...", style="bright_black"),
            Text("-"), Text("-"), Text("-"),
        )

    root.add_row(
        Panel(
            num_tbl,
            title=Text("NOMOR", style="bright_black"),
            border_style="bright_black",
            padding=(0, 0),
        )
    )

    # ── Log ───────────────────────────────────────────────────────────────
    log_tbl = Table(box=None, show_header=False, expand=True, padding=(0, 0))
    log_tbl.add_column("ts",  width=10)
    log_tbl.add_column("lvl", width=5)
    log_tbl.add_column("msg", ratio=1, overflow="fold")

    with lock:
        recent = log_msgs[-8:]

    for entry_ts, lvl, msg in reversed(recent):
        log_tbl.add_row(
            Text(entry_ts, style="bright_black"),
            Text(lvl,      style=LEVEL_COLOR.get(lvl, "white")),
            Text(msg,      style="bright_black"),
        )

    root.add_row(
        Panel(
            log_tbl,
            title=Text("LOG", style="bright_black"),
            border_style="bright_black",
            padding=(0, 0),
        )
    )

    return root

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────

def main():
    threading.Thread(target=svc_balance, daemon=True).start()
    add_log("Balance service started", "INFO")

    threads = []
    for _ in range(THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    add_log(f"{THREADS} workers started", "INFO")

    with Live(build(), refresh_per_second=3, screen=True,
              vertical_overflow="visible") as live:
        while any(t.is_alive() for t in threads):
            live.update(build())
            time.sleep(0.33)

    console.print(
        Panel(
            Align.center(Text("SELESAI  //  TARGET TERCAPAI", style="bold bright_green")),
            border_style="bright_green",
        )
    )

if __name__ == "__main__":
    main()
