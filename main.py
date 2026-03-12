import requests
import time
import threading
import os

API_KEY = "API_KEY_KAMU"
BASE_URL = "https://hero-sms.com/stubs/handler_api.php"

SERVICE = "wa"
COUNTRY = 54

JUMLAH_NOMOR = 5

MIN_PRICE = 1.3
MAX_PRICE = 1.6

THREADS = 5
ORDER_DELAY = 0.5

OTP_CHECK_DELAY = 3

# TELEGRAM
BOT_TOKEN = "TOKEN_BOT_KAMU"
CHAT_ID = "CHAT_ID_KAMU"

success = 0
retry = 0
cancel = 0
balance = "Loading..."

numbers = []

lock = threading.Lock()


def send_telegram(text):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.get(url, params={
        "chat_id": CHAT_ID,
        "text": text
    })


def get_balance():
    global balance
    while True:
        try:
            r = requests.get(BASE_URL, params={
                "api_key": API_KEY,
                "action": "getBalance"
            })
            balance = r.text
        except:
            balance = "Error"
        time.sleep(5)


def order_number():
    r = requests.get(BASE_URL, params={
        "api_key": API_KEY,
        "action": "getNumber",
        "service": SERVICE,
        "country": COUNTRY
    })
    return r.text


def check_sms(order_id):

    while True:

        r = requests.get(BASE_URL, params={
            "api_key": API_KEY,
            "action": "getStatus",
            "id": order_id
        })

        result = r.text

        if "STATUS_OK" in result:

            otp = result.split(":")[1]

            send_telegram(
                f"✅ OTP MASUK\n\n"
                f"OrderID: {order_id}\n"
                f"OTP: {otp}"
            )

            print("OTP:", otp)

            return

        if "STATUS_CANCEL" in result:
            return

        time.sleep(OTP_CHECK_DELAY)


def cancel_number(order_id):
    requests.get(BASE_URL, params={
        "api_key": API_KEY,
        "action": "setStatus",
        "id": order_id,
        "status": 8
    })


def dashboard():

    while success < JUMLAH_NOMOR:

        os.system("clear")

        print("HERO SMS OTP BOT")
        print("--------------------------------")

        print("\n============= HERO SMS DASHBOARD =============")
        print("Balance :", balance)
        print("Target  :", JUMLAH_NOMOR)
        print("Success :", success)
        print("Retry   :", retry)
        print("Cancel  :", cancel)
        print("==============================================")

        if numbers:
            print("\nNomor Didapat:")
            for n in numbers:
                print("-", n)

        time.sleep(1)


def worker():
    global success, retry, cancel

    while True:

        with lock:
            if success >= JUMLAH_NOMOR:
                return

        result = order_number()

        if "ACCESS_NUMBER" not in result:

            with lock:
                retry += 1

            time.sleep(ORDER_DELAY)
            continue

        data = result.split(":")
        order_id = data[1]
        number = data[2]

        price = 0

        try:
            price = float(data[3])
        except:
            pass

        if price != 0 and (price < MIN_PRICE or price > MAX_PRICE):

            cancel_number(order_id)

            with lock:
                cancel += 1

            continue

        with lock:

            success += 1
            text = f"{number} | {price}"
            numbers.append(text)

        print("Nomor:", number)

        send_telegram(
            f"📱 NOMOR OTP DIDAPAT\n\n"
            f"Nomor: {number}\n"
            f"Harga: {price}\n"
            f"OrderID: {order_id}"
        )

        # cek OTP
        sms_thread = threading.Thread(target=check_sms, args=(order_id,))
        sms_thread.start()

        time.sleep(ORDER_DELAY)


balance_thread = threading.Thread(target=get_balance)
balance_thread.daemon = True
balance_thread.start()

dash_thread = threading.Thread(target=dashboard)
dash_thread.daemon = True
dash_thread.start()

threads = []

for i in range(THREADS):
    t = threading.Thread(target=worker)
    t.start()
    threads.append(t)

for t in threads:
    t.join()

print("\nTarget nomor tercapai!")
