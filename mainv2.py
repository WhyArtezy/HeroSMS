import requests
import time
import threading
import os
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich import box
from rich.layout import Layout
from rich.align import Align

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
API_KEY        = "API_KEY_KAMU"
BASE_URL       = "https://hero-sms.com/stubs/handler_api.php"
SERVICE        = "wa"
COUNTRY        = 54
JUMLAH_NOMOR   = 5
MIN_PRICE      = 0.20
MAX_PRICE      = 0.30
THREADS        = 5
ORDER_DELAY    = 0.3
OTP_CHECK_DELAY = 5

# TELEGRAM
BOT_TOKEN = "TOKEN_BOT_KAMU"
CHAT_ID   = "CHAT_ID_KAMU"

# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────
success  = 0
retry    = 0
cancel   = 0
balance  = "Loading..."
numbers  = []          # list of dicts: {number, price, order_id, otp, status}
log_msgs = []          # activity log

lock = threading.Lock()
console = Console()

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def add_log(msg: str, style: str = "white"):
    ts = datetime.now().strftime("%H:%M:%S")
    with lock:
        log_msgs.append((ts, msg, style))
        if len(log_msgs) > 50:
            log_msgs.pop(0)


def send_telegram(text: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": CHAT_ID, "text": text}, timeout=8)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  BACKGROUND THREADS
# ─────────────────────────────────────────────

def get_balance():
    global balance
    while True:
        try:
            r = requests.get(BASE_URL, params={
                "api_key": API_KEY, "action": "getBalance"
            }, timeout=8)
            balance = r.text.strip()
        except Exception:
            balance = "[red]Error[/red]"
        time.sleep(5)


def order_number():
    r = requests.get(BASE_URL, params={
        "api_key": API_KEY,
        "action": "getNumber",
        "service": SERVICE,
        "country": COUNTRY,
    }, timeout=10)
    return r.text


def check_sms(order_id: str, row_ref: dict):
    while True:
        try:
            r = requests.get(BASE_URL, params={
                "api_key": API_KEY,
                "action": "getStatus",
                "id": order_id,
            }, timeout=10)
            result = r.text

            if "STATUS_OK" in result:
                otp = result.split(":")[1]
                with lock:
                    row_ref["otp"]    = otp
                    row_ref["status"] = "✅ OTP"
                add_log(f"OTP {otp} diterima untuk {row_ref['number']}", "green")
                send_telegram(
                    f"✅ OTP MASUK\n\nOrderID: {order_id}\nOTP: {otp}"
                )
                return

            if "STATUS_CANCEL" in result:
                with lock:
                    row_ref["status"] = "❌ Cancel"
                add_log(f"Order {order_id} dibatalkan", "red")
                return

        except Exception:
            pass
        time.sleep(OTP_CHECK_DELAY)


def cancel_number(order_id: str):
    try:
        requests.get(BASE_URL, params={
            "api_key": API_KEY,
            "action": "setStatus",
            "id": order_id,
            "status": 8,
        }, timeout=8)
    except Exception:
        pass


# ─────────────────────────────────────────────
#  WORKER
# ─────────────────────────────────────────────

def worker():
    global success, retry, cancel

    while True:
        with lock:
            if success >= JUMLAH_NOMOR:
                return

        try:
            result = order_number()
        except Exception as e:
            add_log(f"Request gagal: {e}", "red")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        if "ACCESS_NUMBER" not in result:
            with lock:
                retry += 1
            add_log(f"Gagal order: {result}", "yellow")
            time.sleep(ORDER_DELAY)
            continue

        data     = result.split(":")
        order_id = data[1]
        number   = data[2]

        price = 0.0
        try:
            price = float(data[3])
        except Exception:
            pass

        if price != 0 and (price < MIN_PRICE or price > MAX_PRICE):
            cancel_number(order_id)
            with lock:
                cancel += 1
            add_log(f"Harga {price} di luar range → cancel", "yellow")
            continue

        row = {
            "number":   number,
            "price":    price,
            "order_id": order_id,
            "otp":      "-",
            "status":   "⏳ Menunggu",
        }

        with lock:
            success += 1
            numbers.append(row)

        add_log(f"Nomor didapat: {number} | Harga: {price}", "cyan")
        send_telegram(
            f"📱 NOMOR OTP DIDAPAT\n\nNomor: {number}\nHarga: {price}\nOrderID: {order_id}"
        )

        t = threading.Thread(target=check_sms, args=(order_id, row), daemon=True)
        t.start()

        time.sleep(ORDER_DELAY)


# ─────────────────────────────────────────────
#  RICH DASHBOARD BUILDER
# ─────────────────────────────────────────────

BANNER = r"""
 ██╗  ██╗███████╗██████╗  ██████╗       ███████╗███╗   ███╗███████╗
 ██║  ██║██╔════╝██╔══██╗██╔═══██╗      ██╔════╝████╗ ████║██╔════╝
 ███████║█████╗  ██████╔╝██║   ██║█████╗███████╗██╔████╔██║███████╗
 ██╔══██║██╔══╝  ██╔══██╗██║   ██║╚════╝╚════██║██║╚██╔╝██║╚════██║
 ██║  ██║███████╗██║  ██║╚██████╔╝      ███████║██║ ╚═╝ ██║███████║
 ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝       ╚══════╝╚═╝     ╚═╝╚══════╝
"""


def make_stat_panel(label: str, value, color: str) -> Panel:
    content = Align.center(
        Text(str(value), style=f"bold {color}", justify="center"),
        vertical="middle",
    )
    return Panel(content, title=f"[dim]{label}[/dim]", border_style=color, height=5)


def build_dashboard() -> Layout:
    layout = Layout()

    # ── Header ──────────────────────────────
    banner_text = Text(BANNER, style="bold cyan", justify="center")
    header = Panel(
        Align.center(banner_text),
        border_style="bright_cyan",
        padding=(0, 2),
    )

    # ── Stat cards ──────────────────────────
    pct = int((success / JUMLAH_NOMOR) * 100) if JUMLAH_NOMOR else 0
    bar_filled = pct // 5          # 0-20 blocks
    bar = "[green]" + "█" * bar_filled + "[/green]" + "░" * (20 - bar_filled)

    stat_panels = Columns([
        make_stat_panel("💰 Balance",  balance,        "gold1"),
        make_stat_panel("🎯 Target",   JUMLAH_NOMOR,   "bright_white"),
        make_stat_panel("✅ Success",  success,         "green"),
        make_stat_panel("🔁 Retry",    retry,           "yellow"),
        make_stat_panel("❌ Cancel",   cancel,          "red"),
    ], equal=True, expand=True)

    progress_panel = Panel(
        Align.center(Text(f"{bar}  {pct}%", justify="center")),
        title="[dim]PROGRESS[/dim]",
        border_style="bright_cyan",
        height=5,
    )

    # ── Numbers table ────────────────────────
    tbl = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_cyan",
        header_style="bold bright_cyan",
        show_edge=True,
        expand=True,
    )
    tbl.add_column("#",        justify="center", style="dim",         width=4)
    tbl.add_column("Nomor",    justify="left",   style="bright_white", min_width=16)
    tbl.add_column("Harga",    justify="center", style="gold1",        width=10)
    tbl.add_column("Order ID", justify="center", style="dim",          width=14)
    tbl.add_column("OTP",      justify="center", style="bold green",   width=12)
    tbl.add_column("Status",   justify="center",                        width=14)

    with lock:
        rows_snapshot = list(numbers)

    for i, row in enumerate(rows_snapshot, 1):
        status_style = (
            "green" if "OTP"    in row["status"] else
            "red"   if "Cancel" in row["status"] else
            "yellow"
        )
        tbl.add_row(
            str(i),
            row["number"],
            f"{row['price']:.2f}",
            row["order_id"],
            row["otp"],
            Text(row["status"], style=status_style),
        )

    num_panel = Panel(
        tbl,
        title="[bold bright_cyan]📱  NOMOR AKTIF[/bold bright_cyan]",
        border_style="bright_cyan",
    )

    # ── Activity log ─────────────────────────
    log_table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    log_table.add_column("ts",  style="dim", width=10)
    log_table.add_column("msg", ratio=1)

    with lock:
        recent = log_msgs[-12:]

    for ts, msg, style in reversed(recent):
        log_table.add_row(f"[dim]{ts}[/dim]", Text(msg, style=style))

    log_panel = Panel(
        log_table,
        title="[bold bright_cyan]📋  ACTIVITY LOG[/bold bright_cyan]",
        border_style="bright_cyan",
    )

    # ── Footer ───────────────────────────────
    now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    footer = Panel(
        Align.center(Text(f"⏱  {now}  │  Threads: {THREADS}  │  Service: {SERVICE.upper()}  │  Country: {COUNTRY}", style="dim")),
        border_style="dim",
        height=3,
    )

    # Compose
    layout.split_column(
        Layout(header,         name="header",   size=10),
        Layout(stat_panels,    name="stats",    size=5),
        Layout(progress_panel, name="progress", size=5),
        Layout(num_panel,      name="numbers",  ratio=3),
        Layout(log_panel,      name="log",      ratio=2),
        Layout(footer,         name="footer",   size=3),
    )

    return layout


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    # Start balance poller
    bt = threading.Thread(target=get_balance, daemon=True)
    bt.start()

    # Start workers
    threads = []
    for _ in range(THREADS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    # Live dashboard
    with Live(build_dashboard(), refresh_per_second=2, screen=True) as live:
        while success < JUMLAH_NOMOR or any(t.is_alive() for t in threads):
            live.update(build_dashboard())
            time.sleep(0.5)

    console.print("\n[bold green]✅  Target nomor tercapai! Bot selesai.[/bold green]")


if __name__ == "__main__":
    main()
