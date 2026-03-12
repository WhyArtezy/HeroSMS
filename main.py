import requests
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich import box

# ─────────────────────────────────────────
#         BACA KONFIGURASI DARI FILE
# ─────────────────────────────────────────

def load_config(path="config.txt"):
    cfg = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip()
    return cfg

CFG = load_config()

API_KEY         = CFG["API_KEY"]
BASE_URL        = CFG["BASE_URL"]
SERVICE         = CFG["SERVICE"]
COUNTRY         = int(CFG["COUNTRY"])
JUMLAH_NOMOR    = int(CFG["JUMLAH_NOMOR"])
MIN_PRICE       = float(CFG["MIN_PRICE"])
MAX_PRICE       = float(CFG["MAX_PRICE"])
THREADS         = int(CFG["THREADS"])
ORDER_DELAY     = float(CFG["ORDER_DELAY"])
OTP_CHECK_DELAY = float(CFG["OTP_CHECK_DELAY"])
OTP_TIMEOUT     = int(CFG["OTP_TIMEOUT"])
BALANCE_DELAY   = int(CFG["BALANCE_DELAY"])
REQUEST_TIMEOUT = int(CFG["REQUEST_TIMEOUT"])
BOT_TOKEN       = CFG["BOT_TOKEN"]
CHAT_ID         = CFG["CHAT_ID"]

# ─────────────────────────────────────────
#               STATE GLOBAL
# ─────────────────────────────────────────

console = Console()
lock    = threading.Lock()

success = 0
retry   = 0
cancel  = 0
timeout = 0
balance = "Loading..."

numbers  = []
logs     = []
MAX_LOGS = 20

# Session reuse — jauh lebih cepat dari requests.get biasa
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=THREADS + 5,
    pool_maxsize=THREADS + 10,
    max_retries=2,
)
session.mount("https://", adapter)
session.mount("http://",  adapter)

# ─────────────────────────────────────────
#                 HELPERS
# ─────────────────────────────────────────

def now():
    return datetime.now().strftime("%H:%M:%S")

def add_log(level, message):
    with lock:
        logs.append((now(), level, message))
        if len(logs) > MAX_LOGS:
            logs.pop(0)

LEVEL_STYLE = {
    "INFO":    ("cyan",         "i"),
    "SUCCESS": ("bold green",   "v"),
    "WARNING": ("bold yellow",  "!"),
    "ERROR":   ("bold red",     "x"),
    "OTP":     ("bold magenta", "*"),
    "TIMEOUT": ("bold red",     "T"),
}

# ─────────────────────────────────────────
#               TELEGRAM
# ─────────────────────────────────────────

tg_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tg")

def _tg_send(text):
    try:
        session.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={"chat_id": CHAT_ID, "text": text},
            timeout=8,
        )
    except Exception:
        pass

def send_telegram(text):
    tg_executor.submit(_tg_send, text)

# ─────────────────────────────────────────
#               API CALLS
# ─────────────────────────────────────────

def get_balance():
    global balance
    while True:
        try:
            r = session.get(BASE_URL, params={
                "api_key": API_KEY,
                "action":  "getBalance",
            }, timeout=REQUEST_TIMEOUT)
            raw = r.text.strip()
            if ":" in raw:
                balance = raw.split(":")[1]
            else:
                balance = raw
        except Exception:
            balance = "Error"
        time.sleep(BALANCE_DELAY)

def order_number():
    r = session.get(BASE_URL, params={
        "api_key": API_KEY,
        "action":  "getNumber",
        "service": SERVICE,
        "country": COUNTRY,
    }, timeout=REQUEST_TIMEOUT)
    return r.text

def cancel_number(order_id):
    try:
        session.get(BASE_URL, params={
            "api_key": API_KEY,
            "action":  "setStatus",
            "id":      order_id,
            "status":  8,
        }, timeout=REQUEST_TIMEOUT)
    except Exception:
        pass

def check_sms(order_id, number):
    global timeout
    started = time.time()

    while True:
        elapsed = time.time() - started

        if elapsed >= OTP_TIMEOUT:
            cancel_number(order_id)
            with lock:
                timeout += 1
                for n in numbers:
                    if n["order_id"] == order_id:
                        n["status"] = "Timeout"
                        break
            add_log("TIMEOUT", f"Timeout {OTP_TIMEOUT}s [{number}]")
            send_telegram(f"Timeout\n\nNomor: {number}\nOrderID: {order_id}\nTidak ada OTP dalam {OTP_TIMEOUT}s")
            return

        try:
            r = session.get(BASE_URL, params={
                "api_key": API_KEY,
                "action":  "getStatus",
                "id":      order_id,
            }, timeout=REQUEST_TIMEOUT)
            result = r.text

            if "STATUS_OK" in result:
                otp = result.split(":")[1].strip()
                elapsed_fmt = f"{int(elapsed)}s"
                with lock:
                    for n in numbers:
                        if n["order_id"] == order_id:
                            n["otp"]     = otp
                            n["status"]  = "OTP Masuk"
                            n["elapsed"] = elapsed_fmt
                            break
                add_log("OTP", f"OTP [{number}] -> {otp} ({elapsed_fmt})")
                send_telegram(
                    f"OTP MASUK\n\nNomor: {number}\nOrderID: {order_id}\nOTP: {otp}\nWaktu: {elapsed_fmt}"
                )
                return

            if "STATUS_CANCEL" in result:
                with lock:
                    for n in numbers:
                        if n["order_id"] == order_id:
                            n["status"] = "Cancel"
                            break
                add_log("WARNING", f"Server batalkan {number}")
                return

        except requests.exceptions.Timeout:
            add_log("WARNING", f"Timeout cek OTP [{number}]")
        except Exception as e:
            add_log("ERROR", f"Error cek OTP [{number}]: {e}")

        time.sleep(OTP_CHECK_DELAY)

# ─────────────────────────────────────────
#               WORKER
# ─────────────────────────────────────────

def worker():
    global success, retry, cancel

    while True:
        with lock:
            if success >= JUMLAH_NOMOR:
                return

        try:
            result = order_number()
        except requests.exceptions.Timeout:
            add_log("WARNING", "Request order timeout")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue
        except Exception as e:
            add_log("ERROR", f"Request gagal: {e}")
            with lock:
                retry += 1
            time.sleep(ORDER_DELAY)
            continue

        if "ACCESS_NUMBER" not in result:
            add_log("WARNING", f"Gagal -> {result.strip()[:40]}")
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

        if price != 0 and not (MIN_PRICE <= price <= MAX_PRICE):
            cancel_number(order_id)
            add_log("WARNING", f"Harga {price} di luar range")
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
                "status":   "Aktif",
                "time":     now(),
                "elapsed":  "-",
            })

        add_log("SUCCESS", f"Nomor: {number} | Harga: {price}")
        send_telegram(f"NOMOR DIDAPAT\n\nNomor: {number}\nHarga: {price}\nOrderID: {order_id}")

        threading.Thread(
            target=check_sms,
            args=(order_id, number),
            daemon=True,
            name=f"otp-{order_id}",
        ).start()

        time.sleep(ORDER_DELAY)

# ─────────────────────────────────────────
#                  UI
# ─────────────────────────────────────────

BANNER = r"""
 ██╗  ██╗███████╗██████╗  ██████╗     ███████╗███╗   ███╗███████╗
 ██║  ██║██╔════╝██╔══██╗██╔═══██╗    ██╔════╝████╗ ████║██╔════╝
 ███████║█████╗  ██████╔╝██║   ██║    ███████╗██╔████╔██║███████╗
 ██╔══██║██╔══╝  ██╔══██╗██║   ██║    ╚════██║██║╚██╔╝██║╚════██║
 ██║  ██║███████╗██║  ██║╚██████╔╝    ███████║██║ ╚═╝ ██║███████║
 ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝    ╚══════╝╚═╝     ╚═╝╚══════╝
"""

def build_header():
    return Panel(Text(BANNER, style="bold cyan", justify="center"), style="cyan", padding=(0, 2))

def build_stats():
    pct        = int((success / JUMLAH_NOMOR) * 100) if JUMLAH_NOMOR else 0
    bar_filled = int(pct / 5)
    bar        = "[green]" + "█" * bar_filled + "[/green]" + "░" * (20 - bar_filled)

    g = Table.grid(expand=True, padding=(0, 3))
    for _ in range(6):
        g.add_column(justify="center")

    g.add_row(
        f"[bold white]SALDO[/bold white]\n[bold yellow]{balance}[/bold yellow]",
        f"[bold white]TARGET[/bold white]\n[bold cyan]{success}[/bold cyan][white]/{JUMLAH_NOMOR}[/white]",
        f"[bold white]RETRY[/bold white]\n[bold yellow]{retry}[/bold yellow]",
        f"[bold white]CANCEL[/bold white]\n[bold red]{cancel}[/bold red]",
        f"[bold white]TIMEOUT[/bold white]\n[bold red]{timeout}[/bold red]",
        f"[bold white]PROGRESS[/bold white]\n{bar} [bold]{pct}%[/bold]",
    )
    return Panel(g, title="[bold cyan]STATUS[/bold cyan]", style="cyan", box=box.ROUNDED)

def build_config_bar():
    g = Table.grid(expand=True, padding=(0, 4))
    for _ in range(5):
        g.add_column(justify="center")
    g.add_row(
        f"[dim]Threads[/dim] [bold]{THREADS}[/bold]",
        f"[dim]Order delay[/dim] [bold]{ORDER_DELAY}s[/bold]",
        f"[dim]OTP poll[/dim] [bold]{OTP_CHECK_DELAY}s[/bold]",
        f"[dim]OTP timeout[/dim] [bold]{OTP_TIMEOUT}s[/bold]",
        f"[dim]Service[/dim] [bold]{SERVICE.upper()}[/bold]",
    )
    return Panel(g, title="[bold cyan]KONFIGURASI AKTIF[/bold cyan]", style="dim cyan", box=box.ROUNDED)

def build_numbers_table():
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        expand=True,
        row_styles=["", "dim"],
    )
    table.add_column("#",        width=4,  justify="center")
    table.add_column("Nomor",    width=20)
    table.add_column("Harga",    width=8,  justify="right")
    table.add_column("Order ID", width=13, justify="center")
    table.add_column("OTP",      width=14, justify="center")
    table.add_column("Status",   width=12, justify="center")
    table.add_column("Masuk",    width=8,  justify="center")
    table.add_column("Tunggu",   width=8,  justify="center")

    with lock:
        data = list(numbers)

    for i, n in enumerate(data, 1):
        otp_style = "bold magenta" if n["otp"] != "Menunggu..." else "dim"
        status_style = "bold green" if "OTP" in n["status"] else "bold red" if n["status"] in ("Timeout","Cancel") else "yellow"
        table.add_row(
            str(i),
            f"[bold green]{n['number']}[/bold green]",
            f"[yellow]{n['price']}[/yellow]",
            f"[dim]{n['order_id']}[/dim]",
            f"[{otp_style}]{n['otp']}[/{otp_style}]",
            f"[{status_style}]{n['status']}[/{status_style}]",
            f"[dim]{n['time']}[/dim]",
            f"[dim]{n.get('elapsed', '-')}[/dim]",
        )

    return Panel(table, title="[bold cyan]NOMOR AKTIF[/bold cyan]", style="cyan", box=box.ROUNDED)

def build_logs():
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(width=8,  justify="center")
    table.add_column(width=2,  justify="center")
    table.add_column(width=10, justify="center")
    table.add_column()

    with lock:
        recent = list(logs[-12:])

    for (ts, level, msg) in reversed(recent):
        style, icon = LEVEL_STYLE.get(level, ("white", "-"))
        table.add_row(
            f"[dim]{ts}[/dim]",
            f"[{style}]{icon}[/{style}]",
            f"[{style}]{level}[/{style}]",
            msg,
        )

    return Panel(table, title="[bold cyan]LOG AKTIVITAS[/bold cyan]", style="cyan", box=box.ROUNDED)

def render_dashboard():
    layout = Layout()
    layout.split_column(
        Layout(build_header(),        name="header",  size=9),
        Layout(build_stats(),         name="stats",   size=6),
        Layout(build_config_bar(),    name="cfg",     size=5),
        Layout(build_numbers_table(), name="numbers", size=max(6, len(numbers) + 5)),
        Layout(build_logs(),          name="logs",    size=16),
        Layout(
            Align.center(Text(
                f"  Threads:{THREADS}  OTP Timeout:{OTP_TIMEOUT}s  "
                f"Order Delay:{ORDER_DELAY}s  OTP Poll:{OTP_CHECK_DELAY}s  ",
                style="bold cyan on black",
            )),
            name="footer", size=1,
        ),
    )
    return layout

# ─────────────────────────────────────────
#                  MAIN
# ─────────────────────────────────────────

if __name__ == "__main__":

    threading.Thread(target=get_balance, daemon=True, name="balance").start()

    add_log("INFO", "Config dimuat dari config.txt")
    add_log("INFO", f"Target: {JUMLAH_NOMOR} nomor | Threads: {THREADS}")
    add_log("INFO", f"Harga: {MIN_PRICE}-{MAX_PRICE} | OTP timeout: {OTP_TIMEOUT}s")

    workers = []
    for i in range(THREADS):
        t = threading.Thread(target=worker, daemon=False, name=f"worker-{i+1}")
        t.start()
        workers.append(t)

    add_log("SUCCESS", f"{THREADS} worker thread dimulai")

    with Live(render_dashboard(), refresh_per_second=4, screen=True) as live:
        while any(t.is_alive() for t in workers):
            live.update(render_dashboard())
            time.sleep(0.25)
        live.update(render_dashboard())
        time.sleep(1)

    tg_executor.shutdown(wait=False)

    console.print()
    console.print(Panel(
        f"[bold green]SELESAI![/bold green]  "
        f"{success} nomor berhasil | {timeout} timeout | {cancel} dibatalkan",
        style="bold green",
        box=box.DOUBLE,
    ))
