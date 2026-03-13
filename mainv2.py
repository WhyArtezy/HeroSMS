import requests
import time
import threading
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich import box
from rich.layout import Layout

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
        if len(log_msgs) > 100:
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
#  BACKGROUND SERVICES
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
            balance = r.text.strip()
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
            params={
                "api_key": API_KEY,
                "action":  "setStatus",
                "id":      order_id,
                "status":  8,
            },
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
                    row["status"] = "RECEIVED"
                add_log(f"OTP {otp} masuk  [{row['number']}]", "OK")
                send_telegram(f"OTP MASUK\n\nOrderID : {order_id}\nOTP     : {otp}")
                return
            if "STATUS_CANCEL" in result:
                with lock:
                    row["status"] = "CANCELLED"
                add_log(f"Order {order_id} dibatalkan server", "WARN")
                return
        except Exception as e:
            add_log(f"check_sms error: {e}", "ERR")
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
            add_log(f"Request error: {e}", "ERR")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        if "ACCESS_NUMBER" not in result:
            with lock:
                retry += 1
            add_log(f"Order gagal: {result}", "WARN")
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
            add_log(f"Harga {price:.2f} di luar range -> cancel", "WARN")
            continue

        row = {
            "ts":       ts(),
            "number":   number,
            "price":    price,
            "order_id": order_id,
            "otp":      "-",
            "status":   "WAITING",
        }

        with lock:
            success += 1
            numbers.append(row)

        add_log(f"Nomor {number}  harga {price:.2f}  id {order_id}", "OK")
        send_telegram(
            f"NOMOR DIDAPAT\n\nNomor   : {number}\nHarga   : {price}\nOrderID : {order_id}"
        )

        t = threading.Thread(target=check_sms, args=(order_id, row), daemon=True)
        t.start()
        time.sleep(ORDER_DELAY)

# ─────────────────────────────────────────
#  DASHBOARD BUILDER
# ─────────────────────────────────────────

LOGO = (
    " ██╗  ██╗███████╗██████╗  ██████╗      ███████╗███╗   ███╗███████╗\n"
    " ██║  ██║██╔════╝██╔══██╗██╔═══██╗     ██╔════╝████╗ ████║██╔════╝\n"
    " ███████║█████╗  ██████╔╝██║   ██║     ███████╗██╔████╔██║███████╗\n"
    " ██╔══██║██╔══╝  ██╔══██╗██║   ██║     ╚════██║██║╚██╔╝██║╚════██║\n"
    " ██║  ██║███████╗██║  ██║╚██████╔╝     ███████║██║ ╚═╝ ██║███████║\n"
    " ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝     ╚══════╝╚═╝     ╚═╝╚══════╝"
)

def _stat_block(label: str, value, hi: str = "bright_green") -> Panel:
    body = Align.center(Text(str(value), style=f"bold {hi}"), vertical="middle")
    return Panel(
        body,
        title=f"[dim]{label}[/dim]",
        border_style="bright_black",
        padding=(0, 2),
        height=5,
    )

def build() -> Layout:
    # header
    header_text = Text(justify="center")
    header_text.append(LOGO + "\n", style="bold bright_green")
    header_text.append(
        f"\n  OTP AUTOMATION   //   SERVICE:{SERVICE.upper()}   COUNTRY:{COUNTRY}   THREADS:{THREADS}\n",
        style="dim",
    )
    header = Panel(
        Align.center(header_text),
        border_style="bright_green",
        padding=(0, 4),
    )

    # stats
    stats = Columns(
        [
            _stat_block("BALANCE",  balance,       "gold1"),
            _stat_block("TARGET",   JUMLAH_NOMOR,  "bright_white"),
            _stat_block("SUCCESS",  success,        "bright_green"),
            _stat_block("RETRY",    retry,          "yellow"),
            _stat_block("CANCEL",   cancel,         "red"),
        ],
        equal=True,
        expand=True,
    )

    # progress bar
    done_pct = (success / JUMLAH_NOMOR * 100) if JUMLAH_NOMOR else 0
    blocks   = int(done_pct / 5)
    bar = Text()
    bar.append("[", style="dim")
    bar.append("=" * blocks,        style="bold bright_green")
    bar.append("-" * (20 - blocks), style="dim bright_black")
    bar.append(f"]  {done_pct:5.1f}%", style="bright_white")
    progress_panel = Panel(
        Align.center(bar, vertical="middle"),
        title="[dim]PROGRESS[/dim]",
        border_style="bright_black",
        height=5,
    )

    # numbers table
    STATUS_STYLE = {
        "RECEIVED":  "bold bright_green",
        "CANCELLED": "bold red",
        "WAITING":   "yellow",
    }
    num_tbl = Table(
        box=box.SIMPLE,
        border_style="bright_black",
        header_style="dim bright_green",
        show_edge=True,
        expand=True,
        padding=(0, 1),
    )
    num_tbl.add_column("NO",       justify="center", width=4,  style="dim")
    num_tbl.add_column("TIME",     justify="center", width=10, style="dim")
    num_tbl.add_column("NOMOR",    justify="left",   min_width=15, style="bright_white")
    num_tbl.add_column("HARGA",    justify="right",  width=8,  style="yellow")
    num_tbl.add_column("ORDER ID", justify="center", width=14, style="dim")
    num_tbl.add_column("OTP",      justify="center", width=12, style="bold bright_green")
    num_tbl.add_column("STATUS",   justify="center", width=12)

    with lock:
        rows_snap = list(numbers)
    for i, r in enumerate(rows_snap, 1):
        num_tbl.add_row(
            str(i), r["ts"], r["number"],
            f"{r['price']:.2f}", r["order_id"], r["otp"],
            Text(r["status"], style=STATUS_STYLE.get(r["status"], "dim")),
        )

    num_panel = Panel(
        num_tbl,
        title="[dim bright_green]NOMOR AKTIF[/dim bright_green]",
        border_style="bright_black",
    )

    # log
    LEVEL_STYLE = {
        "OK":   "bold bright_green",
        "WARN": "bold yellow",
        "ERR":  "bold red",
        "INFO": "dim white",
    }
    log_tbl = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    log_tbl.add_column("TS",  style="dim", width=10)
    log_tbl.add_column("LVL", width=6)
    log_tbl.add_column("MSG", ratio=1)

    with lock:
        recent = log_msgs[-14:]
    for t, lvl, msg in reversed(recent):
        log_tbl.add_row(t, Text(lvl, style=LEVEL_STYLE.get(lvl, "white")), msg)

    log_panel = Panel(
        log_tbl,
        title="[dim bright_green]ACTIVITY LOG[/dim bright_green]",
        border_style="bright_black",
    )

    # footer
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    footer = Panel(
        Align.center(
            Text(
                f"HERO-SMS-BOT   //   {now}   //   PRICE RANGE: {MIN_PRICE} - {MAX_PRICE}",
                style="dim",
            )
        ),
        border_style="bright_black",
        height=3,
    )

    layout = Layout()
    layout.split_column(
        Layout(header,         name="header",   size=12),
        Layout(stats,          name="stats",    size=5),
        Layout(progress_panel, name="progress", size=5),
        Layout(num_panel,      name="numbers",  ratio=3),
        Layout(log_panel,      name="log",      ratio=2),
        Layout(footer,         name="footer",   size=3),
    )
    return layout

# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────

def main():
    threading.Thread(target=svc_balance, daemon=True).start()
    add_log("Balance service aktif", "INFO")

    threads = []
    for _ in range(THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    add_log(f"{THREADS} worker thread dimulai", "INFO")

    with Live(build(), refresh_per_second=4, screen=True) as live:
        while any(t.is_alive() for t in threads):
            live.update(build())
            time.sleep(0.25)

    console.print(
        Panel(
            Align.center(
                Text("SELESAI  //  TARGET NOMOR TERCAPAI", style="bold bright_green")
            ),
            border_style="bright_green",
        )
    )

if __name__ == "__main__":
    main()
