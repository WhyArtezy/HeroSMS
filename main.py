import requests
import time
import threading
import os
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.columns import Columns
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box
from rich.align import Align
from rich.style import Style

# ─────────────────────────────────────────
#              KONFIGURASI
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

BOT_TOKEN       = "TOKEN_BOT_KAMU"
CHAT_ID         = "CHAT_ID_KAMU"

# ─────────────────────────────────────────

console  = Console()
lock     = threading.Lock()

success  = 0
retry    = 0
cancel   = 0
balance  = "Loading..."

numbers  = []   # list of dict: {number, price, order_id, otp, status, time}
logs     = []   # list of (timestamp, level, message)

MAX_LOGS = 20


# ─────────────────────────────── helpers ───────────────────────────────

def now():
    return datetime.now().strftime("%H:%M:%S")


def add_log(level: str, message: str):
    """level: INFO | SUCCESS | WARNING | ERROR | OTP"""
    with lock:
        logs.append((now(), level, message))
        if len(logs) > MAX_LOGS:
            logs.pop(0)


LEVEL_STYLE = {
    "INFO":    ("cyan",          "ℹ"),
    "SUCCESS": ("bold green",    "✅"),
    "WARNING": ("bold yellow",   "⚠"),
    "ERROR":   ("bold red",      "✗"),
    "OTP":     ("bold magenta",  "🔑"),
}


# ─────────────────────────────── Telegram ──────────────────────────────

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.get(url, params={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception:
        add_log("WARNING", "Gagal kirim Telegram")


# ─────────────────────────────── API calls ─────────────────────────────

def get_balance():
    global balance
    while True:
        try:
            r = requests.get(BASE_URL, params={"api_key": API_KEY, "action": "getBalance"}, timeout=10)
            balance = r.text.strip()
        except Exception:
            balance = "Error"
        time.sleep(5)


def order_number():
    r = requests.get(BASE_URL, params={
        "api_key": API_KEY,
        "action":  "getNumber",
        "service": SERVICE,
        "country": COUNTRY,
    }, timeout=15)
    return r.text


def check_sms(order_id, number):
    while True:
        try:
            r = requests.get(BASE_URL, params={
                "api_key": API_KEY,
                "action":  "getStatus",
                "id":      order_id,
            }, timeout=10)
            result = r.text

            if "STATUS_OK" in result:
                otp = result.split(":")[1].strip()

                with lock:
                    for n in numbers:
                        if n["order_id"] == order_id:
                            n["otp"]    = otp
                            n["status"] = "✅ OTP"
                            break

                add_log("OTP", f"OTP masuk [{number}] → {otp}")
                send_telegram(
                    f"✅ OTP MASUK\n\nNomor: {number}\nOrderID: {order_id}\nOTP: {otp}"
                )
                return

            if "STATUS_CANCEL" in result:
                with lock:
                    for n in numbers:
                        if n["order_id"] == order_id:
                            n["status"] = "❌ Cancel"
                            break
                add_log("WARNING", f"Nomor {number} dibatalkan oleh server")
                return

        except Exception:
            add_log("ERROR", f"Gagal cek SMS untuk {number}")

        time.sleep(OTP_CHECK_DELAY)


def cancel_number(order_id):
    requests.get(BASE_URL, params={
        "api_key": API_KEY,
        "action":  "setStatus",
        "id":      order_id,
        "status":  8,
    }, timeout=10)


# ─────────────────────────────── worker ────────────────────────────────

def worker():
    global success, retry, cancel

    while True:
        with lock:
            if success >= JUMLAH_NOMOR:
                return

        try:
            result = order_number()
        except Exception as e:
            add_log("ERROR", f"Request gagal: {e}")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        if "ACCESS_NUMBER" not in result:
            add_log("WARNING", f"Gagal pesan nomor → {result.strip()}")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        data     = result.split(":")
        order_id = data[1]
        number   = data[2]
        price    = 0.0

        try:
            price = float(data[3])
        except Exception:
            pass

        if price != 0 and (price < MIN_PRICE or price > MAX_PRICE):
            cancel_number(order_id)
            add_log("WARNING", f"Harga {price} di luar range → dibatalkan")
            with lock:
                cancel += 1
            continue

        with lock:
            success += 1
            numbers.append({
                "number":   number,
                "price":    price,
                "order_id": order_id,
                "otp":      "Menunggu...",
                "status":   "⏳ Aktif",
                "time":     now(),
            })

        add_log("SUCCESS", f"Nomor didapat: {number} | Harga: {price}")
        send_telegram(
            f"📱 NOMOR OTP DIDAPAT\n\nNomor: {number}\nHarga: {price}\nOrderID: {order_id}"
        )

        sms_thread = threading.Thread(target=check_sms, args=(order_id, number), daemon=True)
        sms_thread.start()

        time.sleep(ORDER_DELAY)


# ─────────────────────────────── UI builder ────────────────────────────

BANNER = r"""
 ██╗  ██╗███████╗██████╗  ██████╗     ███████╗███╗   ███╗███████╗
 ██║  ██║██╔════╝██╔══██╗██╔═══██╗    ██╔════╝████╗ ████║██╔════╝
 ███████║█████╗  ██████╔╝██║   ██║    ███████╗██╔████╔██║███████╗
 ██╔══██║██╔══╝  ██╔══██╗██║   ██║    ╚════██║██║╚██╔╝██║╚════██║
 ██║  ██║███████╗██║  ██║╚██████╔╝    ███████║██║ ╚═╝ ██║███████║
 ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝    ╚══════╝╚═╝     ╚═╝╚══════╝
"""


def build_header():
    txt = Text(BANNER, style="bold cyan", justify="center")
    return Panel(txt, style="cyan", padding=(0, 2))


def build_stats():
    pct = int((success / JUMLAH_NOMOR) * 100) if JUMLAH_NOMOR else 0
    bar_filled = int(pct / 5)
    bar = "[green]" + "█" * bar_filled + "[/green]" + "░" * (20 - bar_filled)

    stats = Table.grid(expand=True, padding=(0, 3))
    stats.add_column(justify="center")
    stats.add_column(justify="center")
    stats.add_column(justify="center")
    stats.add_column(justify="center")
    stats.add_column(justify="center")

    stats.add_row(
        f"[bold white]💰 SALDO[/bold white]\n[bold yellow]{balance}[/bold yellow]",
        f"[bold white]🎯 TARGET[/bold white]\n[bold cyan]{success}[/bold cyan][white]/{JUMLAH_NOMOR}[/white]",
        f"[bold white]🔄 RETRY[/bold white]\n[bold yellow]{retry}[/bold yellow]",
        f"[bold white]❌ CANCEL[/bold white]\n[bold red]{cancel}[/bold red]",
        f"[bold white]📊 PROGRESS[/bold white]\n{bar} [bold]{pct}%[/bold]",
    )

    return Panel(stats, title="[bold cyan]◆ STATUS[/bold cyan]", style="cyan", box=box.ROUNDED)


def build_numbers_table():
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        row_styles=["", "dim"],
    )
    table.add_column("#",        width=4,  justify="center")
    table.add_column("Nomor",    width=20, justify="left")
    table.add_column("Harga",    width=10, justify="right")
    table.add_column("Order ID", width=14, justify="center")
    table.add_column("OTP",      width=16, justify="center")
    table.add_column("Status",   width=14, justify="center")
    table.add_column("Waktu",    width=10, justify="center")

    with lock:
        data = list(numbers)

    for i, n in enumerate(data, 1):
        otp_style = "bold magenta" if n["otp"] != "Menunggu..." else "dim"
        table.add_row(
            str(i),
            f"[bold green]{n['number']}[/bold green]",
            f"[yellow]{n['price']}[/yellow]",
            f"[dim]{n['order_id']}[/dim]",
            f"[{otp_style}]{n['otp']}[/{otp_style}]",
            n["status"],
            f"[dim]{n['time']}[/dim]",
        )

    return Panel(table, title="[bold cyan]◆ NOMOR AKTIF[/bold cyan]", style="cyan", box=box.ROUNDED)


def build_logs():
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=8,  justify="center")
    table.add_column(width=2,  justify="center")
    table.add_column(width=10, justify="center")
    table.add_column()

    with lock:
        recent = list(logs[-12:])

    for (ts, level, msg) in reversed(recent):
        style, icon = LEVEL_STYLE.get(level, ("white", "•"))
        table.add_row(
            f"[dim]{ts}[/dim]",
            f"[{style}]{icon}[/{style}]",
            f"[{style}]{level}[/{style}]",
            msg,
        )

    return Panel(table, title="[bold cyan]◆ LOG AKTIVITAS[/bold cyan]", style="cyan", box=box.ROUNDED)


def build_footer():
    txt = Text.assemble(
        ("  Hero SMS OTP Bot  ", "bold cyan on black"),
        ("  v1.0  ", "white on black"),
        (f"  Threads: {THREADS}  ", "bold yellow on black"),
        (f"  Service: {SERVICE.upper()}  ", "bold green on black"),
        (f"  Country: {COUNTRY}  ", "bold magenta on black"),
    )
    return Align.center(txt)


def render_dashboard():
    layout = Layout()
    layout.split_column(
        Layout(build_header(),        name="header",  size=9),
        Layout(build_stats(),         name="stats",   size=6),
        Layout(build_numbers_table(), name="numbers", size=max(6, len(numbers) + 5)),
        Layout(build_logs(),          name="logs",    size=16),
        Layout(build_footer(),        name="footer",  size=1),
    )
    return layout


# ─────────────────────────────── main ──────────────────────────────────

if __name__ == "__main__":

    # balance thread
    t_bal = threading.Thread(target=get_balance, daemon=True)
    t_bal.start()

    add_log("INFO", f"Bot dimulai | Target: {JUMLAH_NOMOR} nomor | Threads: {THREADS}")
    add_log("INFO", f"Range harga: {MIN_PRICE} – {MAX_PRICE}")

    # worker threads
    workers = []
    for i in range(THREADS):
        t = threading.Thread(target=worker, daemon=False)
        t.start()
        workers.append(t)
        add_log("INFO", f"Worker #{i+1} dimulai")

    # live dashboard
    with Live(render_dashboard(), refresh_per_second=2, screen=True) as live:
        while any(t.is_alive() for t in workers):
            live.update(render_dashboard())
            time.sleep(0.5)
        # final render
        live.update(render_dashboard())
        time.sleep(1)

    console.print()
    console.print(Panel(
        f"[bold green]✅ SELESAI![/bold green]  {success} nomor berhasil dikumpulkan.",
        style="bold green",
        box=box.DOUBLE,
    ))
