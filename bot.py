# -*- coding: utf-8 -*-
"""
NEWBOT — Bot Telegram nạp tiền cá nhân
=======================================
Chức năng:
  • Đăng ký user, lưu balance vào SQLite (NEWBOT/bot.db).
  • Nút "Nạp Tiền" → hiện QR VietQR có sẵn nội dung CK + tên chủ TK.
  • Nội dung CK ĐA DẠNG: "Mua Ao 12345", "Doi Tien 12345", "Mua Mi Tom 12345"...
    miễn nội dung CK chứa Telegram ID là nhận diện được.
  • Tự động cộng tiền 10-15s/lần qua SePay polling.
  • Nạp ≥ 15.000đ: tự cộng. Nạp < 15.000đ: thông báo nạp sai hạn mức,
    KHÔNG được cộng và KHÔNG hoàn lại.
  • Admin có lệnh /cong, /tru, /sodu, /recent.

Chạy:
  pip install -r requirements.txt
  python bot.py
"""
import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
import sqlite3
import threading
import time
import urllib.parse
import requests
import re
import os
import sys
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# ================== CẤU HÌNH ==================
BOT_TOKEN = "8640300423:AAEs8SiHbb61nMnvOUCT9balvAMM8E3wZj0"
ADMIN_ID  = 5724397112

# Thông tin nhận tiền (đang dùng chung tài khoản với BOT THUANBOT cũ)
BANK_ACC    = "02370392316555"
BANK_NAME   = "MBBANK"
BANK_HOLDER = "NGUYEN TIEN THUAN"

# SePay token (dùng chung với bot cũ — cùng tài khoản nhận tiền)
SEPAY_TOKEN = "OW6VR1JXURDPGQWHAQ8KA9VKMHJIZIQNLSDSLZUC4PBE23LXGEBHKXDOKZCX6IMP"

# Quy định nạp tiền
MIN_DEPOSIT = 15000  # VNĐ — dưới mức này không được cộng và không hoàn

# Giá sản phẩm mặc định (admin có thể chỉnh trong /admin)
PRICE_SETTINGS = [
    ("price_reg_kjc", "REG KJC / 1 nick", 1234),
    ("price_reg_okvip_simple", "REG OKVIP chỉ tạo / 1 nick", 1234),
    ("price_reg_okvip_full", "REG OKVIP xác thực + KM / 1 nick", 4999),
    ("price_okvip_promo", "Xin lại/check KM OKVIP / 1 nick", 39),
    ("price_rent", "Thuê OTP / 1 số", 3500),
    ("price_databank", "Data Bank / 1 data", 5000),
    ("price_shopacc", "Shop ACC Tân Thủ / 1 acc", 16999),
    ("price_proxy_static", "Proxy Tĩnh / 1 ngày", 2345),
]
PRICE_DEFAULTS = {key: str(default) for key, _label, default in PRICE_SETTINGS}
PRICE_LABELS = {key: label for key, label, _default in PRICE_SETTINGS}

# Cú pháp gợi ý — bot match LINH HOẠT (chỉ cần nội dung chứa Telegram ID là OK).
# Mỗi lần user ấn Nạp Tiền sẽ random 1 cú pháp khác nhau để tránh trùng lặp.
SUGGESTED_SYNTAXES = [
    "Mua Ao", "Doi Tien", "Mua Mi Tom", "Nap Tien",
    "Chuyen Khoan", "Mua Hang", "Thanh Toan", "Goi Y",
    "Tang Qua", "Mua Bia", "Mua Sach", "Tra Tien",
]

# API Keys và cấu hình dịch vụ mới
VIOTP_TOKEN = "b5f70a870ef8437ab55b8e98968bc215"
PROXY_VN_KEY = "ArgGysxEnqQTiEkRjIkbPn"

# Cấu hình dịch vụ thuê OTP
SERVICES = {
    "OKVIP": {"id": 1, "price": 3500, "networks": "Viettel"},
    "CM88": {"id": 2, "price": 3500, "networks": "Viettel"},
    "F168": {"id": 3, "price": 3500, "networks": "Viettel"},
    "SC88": {"id": 4, "price": 3500, "networks": "Viettel"},
    "FLY88": {"id": 5, "price": 3500, "networks": "Viettel"},
    "C168": {"id": 6, "price": 3500, "networks": "Viettel"},
    "78WIN": {"id": 7, "price": 3500, "networks": "Viettel"},
}

# Import ViOTP API
try:
    from viotp import ViOTP
    viotp_api = ViOTP(VIOTP_TOKEN)
except Exception as e:
    print(f"Warning: ViOTP unavailable: {e}")
    viotp_api = None

# DB & polling
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
SEPAY_POLL_INTERVAL = 10  # giây
SEPAY_FETCH_LIMIT   = 50

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ================== DATABASE ==================
db_lock = threading.Lock()


def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                first_name  TEXT DEFAULT '',
                username    TEXT DEFAULT '',
                balance     INTEGER DEFAULT 0,
                is_banned   INTEGER DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Backfill cột is_banned cho DB cũ (nếu đã tồn tại trước khi thêm cột)
        try:
            cur.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        except Exception:
            pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sepay_id     TEXT UNIQUE,
                telegram_id  INTEGER,
                amount       INTEGER,
                content      TEXT,
                status       TEXT DEFAULT 'success',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Theo dõi tiền user đã chi tiêu, tách theo loại dịch vụ.
        # Hiện chưa có dịch vụ thật — bảng này dùng cho /admin Check User
        # và sẵn sàng cho các tính năng tiêu tiền sau này (vd: mua acc, mua proxy...).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                amount        INTEGER NOT NULL,
                service_type  TEXT NOT NULL,
                description   TEXT DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Bảng rent_history (Lịch sử thuê OTP ViOTP)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                phone_number TEXT,
                service_name TEXT,
                price INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                rented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bảng data_banks (dữ liệu Bank bán)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS data_banks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank_type TEXT NOT NULL,
                stk TEXT NOT NULL,
                name TEXT NOT NULL,
                price INTEGER DEFAULT 0,
                is_sold INTEGER DEFAULT 0,
                sold_to INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bảng shop_accounts (Shop ACC Tân Thủ)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shop_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                realname TEXT DEFAULT '',
                pin TEXT DEFAULT '111222',
                price INTEGER DEFAULT 0,
                is_sold INTEGER DEFAULT 0,
                sold_to INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bảng proxy_orders (Lịch sử mua Proxy)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS proxy_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                proxy_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bảng user_discounts (Lưu % giảm giá theo định dạng key)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_discounts (
                telegram_id INTEGER,
                item_key TEXT,
                discount_percent INTEGER DEFAULT 0,
                PRIMARY KEY (telegram_id, item_key)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        for table, column, definition in [
            ("rent_history", "price", "INTEGER DEFAULT 0"),
            ("rent_history", "cost", "INTEGER DEFAULT 0"),
            ("rent_history", "rented_at", "TIMESTAMP"),
            ("rent_history", "created_at", "TIMESTAMP"),
            ("data_banks", "price", "INTEGER DEFAULT 0"),
            ("data_banks", "is_sold", "INTEGER DEFAULT 0"),
            ("data_banks", "status", "TEXT DEFAULT 'available'"),
            ("shop_accounts", "price", "INTEGER DEFAULT 0"),
            ("shop_accounts", "is_sold", "INTEGER DEFAULT 0"),
            ("shop_accounts", "status", "TEXT DEFAULT 'available'"),
        ]:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except Exception:
                pass

        cur.execute("UPDATE rent_history SET price=cost WHERE IFNULL(price,0)=0 AND IFNULL(cost,0)>0")
        cur.execute("UPDATE rent_history SET cost=price WHERE IFNULL(cost,0)=0 AND IFNULL(price,0)>0")
        cur.execute("UPDATE rent_history SET rented_at=created_at WHERE rented_at IS NULL AND created_at IS NOT NULL")
        cur.execute("UPDATE rent_history SET created_at=rented_at WHERE created_at IS NULL AND rented_at IS NOT NULL")
        cur.execute("UPDATE data_banks SET is_sold=1 WHERE status='sold'")
        cur.execute("UPDATE data_banks SET status='sold' WHERE is_sold=1")
        cur.execute("UPDATE data_banks SET status='available' WHERE is_sold=0 AND (status IS NULL OR status='')")
        cur.execute("UPDATE shop_accounts SET is_sold=1 WHERE status='sold'")
        cur.execute("UPDATE shop_accounts SET status='sold' WHERE is_sold=1")
        cur.execute("UPDATE shop_accounts SET status='available' WHERE is_sold=0 AND (status IS NULL OR status='')")
        
        # Thêm các settings mới cho dịch vụ SHOP và Thuê OTP
        additional_settings = {
            "price_rent": "3500",
            "price_databank": "5000",
            "price_shopacc": "16999",
            "price_proxy_static": "2345",
            "feat_rent": "1",
            "feat_databank": "1", 
            "feat_shopacc": "1",
            "feat_proxy_static": "1",
            "feat_shop": "1"  # Bật toàn bộ shop
        }
        
        # Combine existing defaults with new settings
        all_settings = {**PRICE_DEFAULTS, **additional_settings}
        
        for key, default_value in all_settings.items():
            cur.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)",
                (key, str(default_value)),
            )
        c.commit()
        c.close()


def get_or_create_user(tid, first_name="", username=""):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
        u = cur.fetchone()
        if not u:
            cur.execute(
                "INSERT INTO users(telegram_id, first_name, username) VALUES (?,?,?)",
                (tid, first_name, username),
            )
            c.commit()
            cur.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
            u = cur.fetchone()
        c.close()
        return dict(u)


def get_balance(tid):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT balance FROM users WHERE telegram_id=?", (tid,))
        r = cur.fetchone()
        c.close()
        return r["balance"] if r else 0


def add_balance(tid, amount):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "UPDATE users SET balance = balance + ? WHERE telegram_id=?",
            (amount, tid),
        )
        c.commit()
        c.close()


def reserve_balance(tid, amount):
    """Trừ giữ tiền an toàn nếu user đủ số dư. Trả (ok, new_balance)."""
    amount = int(amount)
    if amount <= 0:
        return True, get_balance(tid)
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT balance FROM users WHERE telegram_id=?", (tid,))
        r = cur.fetchone()
        current = int(r["balance"]) if r else 0
        if current < amount:
            c.close()
            return False, current
        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE telegram_id=?",
            (amount, tid),
        )
        c.commit()
        new_balance = current - amount
        c.close()
        return True, new_balance


def is_deposit_processed(sepay_id):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT 1 FROM deposits WHERE sepay_id=?", (str(sepay_id),))
        r = cur.fetchone()
        c.close()
        return r is not None


def log_deposit(sepay_id, tid, amount, content, status="success"):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        try:
            cur.execute(
                "INSERT OR IGNORE INTO deposits(sepay_id, telegram_id, amount, content, status) "
                "VALUES (?,?,?,?,?)",
                (str(sepay_id), tid, amount, content, status),
            )
            c.commit()
        except Exception:
            pass
        c.close()


# ----- Các hàm quản lý dịch vụ mới -----
def deduct_balance(tid, amount, service_type):
    """Trừ tiền và ghi log expenses"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT balance FROM users WHERE telegram_id=?", (tid,))
        r = cur.fetchone()
        if not r or r["balance"] < amount:
            c.close()
            return False
        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE telegram_id=?",
            (amount, tid),
        )
        cur.execute(
            "INSERT INTO expenses(telegram_id, amount, service_type, description) "
            "VALUES (?,?,?,?)",
            (tid, amount, service_type, f"Thanh toán {service_type}"),
        )
        c.commit()
        c.close()
        return True


def get_setting(key):
    """Lấy setting từ database"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = cur.fetchone()
        c.close()
        return r["value"] if r else None


def is_feature_enabled(feature_key):
    """Kiểm tra tính năng có được bật không"""
    value = get_setting(f"feat_{feature_key}")
    return value == "1"


def get_user_price(tid, price_key):
    """Lấy giá cho user (có áp dụng discount)"""
    base_price = int(get_setting(price_key) or 0)
    # TODO: Add discount logic later
    return base_price


def add_rent(telegram_id, request_id, phone_number, service_name, cost):
    """Thêm lịch sử thuê OTP"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "INSERT INTO rent_history(telegram_id, request_id, phone_number, service_name, price, cost, status, rented_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (telegram_id, request_id, phone_number, service_name, cost, cost, "pending"),
        )
        c.commit()
        c.close()


def update_rent_status(request_id, status):
    """Cập nhật trạng thái thuê OTP"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "UPDATE rent_history SET status=? WHERE request_id=?",
            (status, request_id),
        )
        c.commit()
        c.close()


def add_data_bank(bank_type, stk, name):
    """Thêm data bank vào kho"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "INSERT INTO data_banks(bank_type, stk, name) VALUES (?,?,?)",
            (bank_type, stk, name),
        )
        c.commit()
        c.close()


def get_available_data_banks_summary():
    """Lấy tóm tắt kho data bank"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT bank_type, COUNT(*) as count FROM data_banks "
            "WHERE is_sold=0 GROUP BY bank_type"
        )
        result = [dict(row) for row in cur.fetchall()]
        c.close()
        return result


def buy_multiple_data_banks(telegram_id, bank_type, amount):
    """Mua nhiều data bank"""
    price = get_user_price(telegram_id, "price_databank")
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT id, stk, name FROM data_banks "
            "WHERE bank_type=? AND is_sold=0 LIMIT ?",
            (bank_type, amount),
        )
        banks = [dict(row) for row in cur.fetchall()]
        
        if len(banks) == amount:
            bank_ids = [b["id"] for b in banks]
            placeholders = ",".join("?" * len(bank_ids))
            cur.execute(
                f"UPDATE data_banks SET is_sold=1, status='sold', sold_to=?, price=? WHERE id IN ({placeholders})",
                [telegram_id, price] + bank_ids,
            )
            c.commit()
        
        c.close()
        return banks if len(banks) == amount else None


def add_shop_accounts_bulk(accounts):
    """Thêm nhiều acc vào shop"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        for acc in accounts:
            cur.execute(
                "INSERT INTO shop_accounts(site, username, password, realname, pin) "
                "VALUES (?,?,?,?,?)",
                (acc["site"], acc["username"], acc["password"], acc["realname"], acc["pin"]),
            )
        c.commit()
        c.close()


def get_shop_accounts_summary():
    """Lấy tóm tắt kho shop acc"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT site, COUNT(*) as count FROM shop_accounts "
            "WHERE is_sold=0 GROUP BY site"
        )
        result = [dict(row) for row in cur.fetchall()]
        c.close()
        return result


def get_shop_account_count(site):
    """Đếm số acc có sẵn trong shop"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT COUNT(*) as count FROM shop_accounts "
            "WHERE site=? AND is_sold=0",
            (site,),
        )
        result = cur.fetchone()
        c.close()
        return result["count"] if result else 0


def buy_shop_accounts(telegram_id, site, amount):
    """Mua acc từ shop"""
    price = get_user_price(telegram_id, "price_shopacc")
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT id, username, password, realname, pin FROM shop_accounts "
            "WHERE site=? AND is_sold=0 LIMIT ?",
            (site, amount),
        )
        accounts = [dict(row) for row in cur.fetchall()]
        
        if len(accounts) == amount:
            account_ids = [a["id"] for a in accounts]
            placeholders = ",".join("?" * len(account_ids))
            cur.execute(
                f"UPDATE shop_accounts SET is_sold=1, status='sold', sold_to=?, price=? WHERE id IN ({placeholders})",
                [telegram_id, price] + account_ids,
            )
            c.commit()
        
        c.close()
        return accounts if len(accounts) == amount else None


def delete_shop_account(account_id):
    """Xóa acc khỏi shop"""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT username FROM shop_accounts WHERE id=?",
            (account_id,),
        )
        result = cur.fetchone()
        if result:
            cur.execute("DELETE FROM shop_accounts WHERE id=?", (account_id,))
            c.commit()
            c.close()
            return dict(result)
        c.close()
        return None


# ----- Hàm xử lý Thuê OTP -----
def get_rent_menu(telegram_id):
    """Tạo menu chọn dịch vụ thuê OTP"""
    markup = InlineKeyboardMarkup()
    srv_list = ["OKVIP", "CM88", "F168", "SC88", "FLY88", "C168", "78WIN"]
    for srv in srv_list:
        config = SERVICES.get(srv, {})
        price = config.get('price', 0)
        markup.row(InlineKeyboardButton(f"📱 {srv} ({price:,}đ)", callback_data=f"rent_{srv}"))
    markup.row(InlineKeyboardButton("🔄 Thuê Lại Số Cũ", callback_data="rent_old_menu"))
    return markup


def get_rent_old_menu(telegram_id):
    """Tạo menu thuê lại số cũ"""
    markup = InlineKeyboardMarkup()
    srv_list = ["OKVIP", "CM88", "F168", "SC88", "FLY88", "C168", "78WIN"]
    for srv in srv_list:
        markup.row(InlineKeyboardButton(f"🔄 Thuê lại {srv}", callback_data=f"rentold_{srv}"))
    markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="rent_back"))
    return markup


def process_rent_old_step(message, service_name):
    """Xử lý nhập số điện thoại để thuê lại"""
    telegram_id = message.chat.id
    text = message.text.strip()
    if text.lower() == '/huy':
        bot.send_message(telegram_id, "❌ Đã hủy thuê lại số.")
        return
    
    if not text.isdigit() or len(text) < 9 or len(text) > 12:
        msg = bot.send_message(telegram_id, "❌ Số điện thoại không hợp lệ. Vui lòng nhập lại (ví dụ: 0987654321) hoặc gõ /huy để hủy:")
        bot.register_next_step_handler(msg, process_rent_old_step, service_name)
        return

    config = SERVICES.get(service_name)
    if not config:
        bot.send_message(telegram_id, "❌ Dịch vụ không khả dụng.")
        return
    
    price = config.get('price', 0)
    process_rent(telegram_id, service_name, config['id'], price, config['networks'], None, text)


def process_rent(telegram_id, service_name, srv_id, price, nets, message_id=None, number=""):
    """Xử lý logic thuê OTP"""
    user = get_or_create_user(telegram_id)
    if not user or user["balance"] < price:
        bot.send_message(telegram_id, f"❌ Bạn không đủ tiền để thuê dịch vụ <b>{service_name}</b> (Cần {price:,} VNĐ). \nVui lòng nạp thêm tiền!")
        return

    if not deduct_balance(telegram_id, price, "Thuê Số"):
        bot.send_message(telegram_id, "❌ Lỗi trừ tiền, có thể số dư của bạn đã thay đổi.")
        return

    # UX: Edit luôn tin nhắn Menu thành Loading thay vì spam tin mới
    loading_text = f"⏳ <i>Đang thực hiện lấy số cho <b>{service_name}</b>, vui lòng chờ...</i>"
    if number:
        loading_text = f"⏳ <i>Đang thực hiện yêu cầu THUÊ LẠI số <b>{number}</b> cho dịch vụ <b>{service_name}</b>, vui lòng chờ...</i>"

    if message_id:
        try: bot.edit_message_text(loading_text, chat_id=telegram_id, message_id=message_id, parse_mode='HTML')
        except: bot.send_message(telegram_id, loading_text, parse_mode='HTML')
    else:
        bot.send_message(telegram_id, loading_text, parse_mode='HTML')

    # Gọi API ViOTP
    try:
        if viotp_api:
            resp = viotp_api.request_service(service_id=srv_id, network=nets, number=number)
            if resp.get("status_code") == 200:
                data = resp.get("data", {})
                phone = data.get("phone_number")
                req_id = data.get("request_id")
                
                add_rent(telegram_id, req_id, phone, service_name, price)
                
                # OKVIP tip logic
                tip_text = "Bạn hãy <b>ẤN GỬI LẠI</b> lần 2 lần 3 nếu 60s không về mã nhé, spam 2-3 lần để tăng tỷ lệ về mã!"
                if service_name != "OKVIP":
                    tip_text = "Hãy ẤN Gửi MÃ nhiều lần để tăng tỷ lệ về mã nhé!"

                bot.send_message(telegram_id, f"✅ Thuê thành công!\n📱 Số của bạn: <code>{phone}</code>\n💬 Đang chờ tin nhắn OTP (Sẽ tự động hoàn tiền nếu hết hạn)..\n💡 <b>TIP</b>: {tip_text}")
                threading.Thread(target=wait_for_otp, args=(telegram_id, req_id, phone, service_name, price)).start()
            else:
                # Hoàn tiền khi lỗi
                add_balance(telegram_id, price)
                err = resp.get("message", "Lỗi không xác định")
                bot.send_message(telegram_id, f"🚫 Có lỗi: <code>{err}</code>\nĐã hoàn lại <b>{price:,} đ</b>.")
        else:
            add_balance(telegram_id, price)
            bot.send_message(telegram_id, "❌ Thư viện ViOTP chưa được cài đặt. Đã hoàn lại tiền.")
    except Exception as e:
        add_balance(telegram_id, price)
        bot.send_message(telegram_id, f"❌ Lỗi kết nối: {str(e)}\nĐã hoàn lại <b>{price:,} đ</b>.")


def wait_for_otp(telegram_id, request_id, phone, service_name, price):
    """Chờ và xử lý OTP"""
    try:
        max_retries = 200  # 200 * 3s = 10 phút chờ tối đa
        for i in range(max_retries):
            time.sleep(3)
            if viotp_api:
                resp = viotp_api.get_session(request_id)
                if resp.get("status_code") == 200:
                    data = resp.get("data", {})
                    status = data.get("Status")
                    
                    if status == 1: 
                        code = data.get("Code")
                        content = data.get("SmsContent")
                        update_rent_status(request_id, "success")
                        msg = f"📩 <b>CÓ TIN NHẮN MỚI</b>\n\n📱 Số: <code>{phone}</code>\n🔖 Dịch vụ: {service_name}\n🔑 <b>CODE OTP:</b> <code>{code}</code>\n📝 Nội dung: <i>{content}</i>"
                        bot.send_message(telegram_id, msg)
                        return
                    elif status == 2: 
                        break 
        
        # Hết hạn, hoàn tiền
        update_rent_status(request_id, "expired")
        add_balance(telegram_id, price)
        bot.send_message(telegram_id, f"⏳ Bỏ qua lấy số {phone} do máy chủ Đối Tác báo hết hạn hoặc không nhận được SMS.\nĐã hoàn lại <b>{price:,} VNĐ</b>.\n🚫 Hết số hãy quay lại sau nhé!")
    except Exception as e:
        update_rent_status(request_id, "error")
        add_balance(telegram_id, price)
        bot.send_message(telegram_id, f"❌ Lỗi khi chờ OTP: {str(e)}\nĐã hoàn lại <b>{price:,} VNĐ</b>.")


def _mua_proxy_vn(loaiproxy="FPT", soluong=1, ngay=1, type_proxy="HTTP"):
    """Gọi API proxy.vn để mua proxy. Trả về list successful proxies hoặc error dict."""
    try:
        url = f"https://proxy.vn/apiv2/muaproxy.php?loaiproxy={loaiproxy}&key={PROXY_VN_KEY}&soluong={soluong}&ngay={ngay}&type={type_proxy}&user=ThuanProxy&password=random"
        resp = requests.get(url, timeout=30)
        data = resp.json()
        
        if isinstance(data, list):
            success_list = []
            for item in data:
                if item.get("status") == 100:
                    success_list.append(item['proxy'])
            
            if success_list:
                return {"status": "success", "proxies": success_list}
            return {"status": "error", "message": "Không tìm thấy proxy thành công trong danh sách trả về"}
        else:
            status = data.get("status")
            if status == 102:
                return {"status": "error", "message": "HẾT PROXY - Vui lòng thử loại khác hoặc quay lại sau"}
            elif status == 103:
                return {"status": "error", "message": "HẾT PROXY THẬT - Loại này đã hết hàng"}
            elif status == 101:
                return {"status": "error", "message": "SAI KEY API"}
            else:
                return {"status": "error", "message": f"Lỗi từ nhà cung cấp: {status}"}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi kết nối API Proxy: {str(e)}"}


# ----- Ban / Unban -----
def is_user_banned(tid):
    """True nếu user đã bị admin chặn dùng bot."""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT is_banned FROM users WHERE telegram_id=?", (tid,))
        r = cur.fetchone()
        c.close()
    return bool(r and r["is_banned"])


def set_user_banned(tid, banned: bool):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "UPDATE users SET is_banned=? WHERE telegram_id=?",
            (1 if banned else 0, tid),
        )
        c.commit()
        c.close()


# ----- Expenses (chi tiêu của user theo dịch vụ) -----
def log_expense(tid, amount, service_type, description=""):
    """Ghi 1 lần user dùng tiền cho dịch vụ. Dùng cho các tính năng sau này."""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "INSERT INTO expenses(telegram_id, amount, service_type, description) "
            "VALUES (?,?,?,?)",
            (tid, int(amount), service_type, description),
        )
        c.commit()
        c.close()


def get_user_expenses_breakdown(tid):
    """Trả về (breakdown_list, total_dict) — chi tiêu theo từng service_type."""
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT service_type, COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS total "
            "FROM expenses WHERE telegram_id=? "
            "GROUP BY service_type ORDER BY total DESC",
            (tid,),
        )
        breakdown = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS total "
            "FROM expenses WHERE telegram_id=?",
            (tid,),
        )
        total = dict(cur.fetchone())
        c.close()
    return breakdown, total


def get_setting(key, default=""):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (key,))
        r = cur.fetchone()
        c.close()
    return r["value"] if r else str(default)


def set_setting(key, value):
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, str(value)),
        )
        c.commit()
        c.close()


def get_price(key):
    try:
        return int(get_setting(key, PRICE_DEFAULTS.get(key, "0")))
    except Exception:
        return int(PRICE_DEFAULTS.get(key, "0"))


def get_user_label(tid):
    """Hiển thị nhãn user thân thiện cho admin: '@username' hoặc 'First Name'.

    Trả về chuỗi rỗng nếu user chưa có trong DB hoặc không có gì để hiển thị.
    """
    with db_lock:
        c = db_conn()
        cur = c.cursor()
        cur.execute(
            "SELECT first_name, username FROM users WHERE telegram_id=?",
            (tid,),
        )
        r = cur.fetchone()
        c.close()
    if not r:
        return ""
    username = (r["username"] or "").strip()
    first = (r["first_name"] or "").strip()
    if username:
        return f"@{username}"
    if first:
        return first
    return ""


# ================== BOT ==================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Tracker: lưu message_id của QR đang hiển thị cho mỗi user
# {telegram_id: message_id}. Mất khi bot restart (chấp nhận được).
active_qr_messages = {}
qr_lock = threading.Lock()


def _delete_old_qr(tid):
    """Xoá tin nhắn QR cũ của user (nếu có), tránh để dồn nhiều QR."""
    with qr_lock:
        old_mid = active_qr_messages.pop(tid, None)
    if old_mid:
        try:
            bot.delete_message(tid, old_mid)
        except Exception:
            pass


def _save_qr_message(tid, message_id):
    with qr_lock:
        active_qr_messages[tid] = message_id


def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("💰 Nạp Tiền"), KeyboardButton("🎁 Thuê Số Ngay"))
    kb.row(KeyboardButton("🛒 SHOP"), KeyboardButton("🚀 Tạo Nick"))
    kb.row(KeyboardButton("👤 Cá Nhân"), KeyboardButton("📖 Hướng Dẫn"))
    kb.row(KeyboardButton("☎️ CSKH"))
    return kb


def _user_allowed(message):
    """Trả về False nếu user đã bị admin chặn dùng bot.

    Khi bị chặn, bot vẫn gửi 1 tin nhắn duy nhất giải thích lý do
    để user biết liên hệ admin.
    """
    tid = message.chat.id
    if tid == ADMIN_ID:
        return True
    if is_user_banned(tid):
        try:
            bot.send_message(
                tid,
                "🚫 <b>TÀI KHOẢN CỦA BẠN ĐÃ BỊ CHẶN</b>\n\n"
                f"Vui lòng liên hệ admin: <a href='tg://user?id={ADMIN_ID}'>BẤM VÀO ĐÂY</a>",
            )
        except Exception:
            pass
        return False
    return True


@bot.message_handler(commands=["start"])
def cmd_start(m):
    if not _user_allowed(m):
        return
    tid = m.chat.id
    get_or_create_user(tid, m.from_user.first_name or "", m.from_user.username or "")
    bot.send_message(
        tid,
        "👋 <b>Chào mừng bạn đến với NEWBOT!</b>\n\n"
        "💰 Nạp tiền nhanh — tự động — an toàn.\n"
        "⚡ Tiền sẽ được cộng tự động sau 10-15 giây.\n\n"
        "Chọn chức năng bên dưới để bắt đầu 👇",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["nap"])
@bot.message_handler(func=lambda m: m.text == "💰 Nạp Tiền")
def menu_nap_tien(m):
    if not _user_allowed(m):
        return
    show_deposit_info(m.chat.id)


def show_deposit_info(tid):
    """Hiện QR VietQR + nội dung CK ngẫu nhiên (chứa Telegram ID).

    Tự động xoá QR cũ của user (nếu có) trước khi gửi QR mới,
    để tránh dồn nhiều mã QR lộn xộn trong khung chat.
    """
    # Xoá QR cũ trước nếu có
    _delete_old_qr(tid)

    syntax_word = random.choice(SUGGESTED_SYNTAXES)
    content = f"{syntax_word} {tid}"
    encoded = urllib.parse.quote(content)

    qr_url = f"https://qr.sepay.vn/img?acc={BANK_ACC}&bank={BANK_NAME}&des={encoded}"

    examples = "\n".join(
        f"   • <code>{s} {tid}</code>" for s in random.sample(SUGGESTED_SYNTAXES, 3)
    )

    caption = (
        "💳 <b>NẠP TIỀN VÀO TÀI KHOẢN</b>\n\n"
        f"🏦 Ngân hàng: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        f"👤 Chủ TK: <b>{BANK_HOLDER}</b>\n"
        f"📝 Nội dung CK: <code>{content}</code>\n\n"
        f"💡 <b>Cách nạp:</b>\n"
        f"   1. Quét QR phía dưới bằng app ngân hàng (đã có sẵn nội dung).\n"
        f"   2. Hoặc CK thủ công với nội dung như ví dụ:\n{examples}\n"
        f"   ⤷ Chỉ cần nội dung chuyển khoản chứa ID Telegram của bạn "
        f"(<code>{tid}</code>) là bot nhận diện được.\n\n"
        f"⚠️ <b>QUY ĐỊNH BẮT BUỘC ĐỌC KỸ:</b>\n"
        f"   • Tối thiểu: <b>{MIN_DEPOSIT:,}đ</b>\n"
        f"   • Bạn có thể nạp <b>BẤT KỲ</b> số tiền nào ≥ {MIN_DEPOSIT:,}đ.\n"
        f"   • Tiền sẽ tự động cộng vào tài khoản sau <b>10–15 giây</b>.\n"
        f"   • ❗️ Nạp dưới <b>{MIN_DEPOSIT:,}đ</b> → KHÔNG được cộng "
        f"và <b>KHÔNG hoàn lại</b>. Vui lòng nạp đúng quy định!"
    )

    sent = None
    try:
        sent = bot.send_photo(tid, qr_url, caption=caption)
    except Exception:
        try:
            sent = bot.send_message(
                tid,
                caption + f"\n\n🔗 Link QR: {qr_url}",
                disable_web_page_preview=False,
            )
        except Exception:
            return

    if sent is not None:
        _save_qr_message(tid, sent.message_id)


@bot.message_handler(func=lambda m: m.text == "👤 Cá Nhân")
def menu_canhan(m):
    if not _user_allowed(m):
        return
    tid = m.chat.id
    user = get_or_create_user(tid, m.from_user.first_name or "", m.from_user.username or "")
    bal = get_balance(tid)
    bot.send_message(
        tid,
        "👤 <b>THÔNG TIN CÁ NHÂN</b>\n\n"
        f"🆔 ID Telegram: <code>{tid}</code>\n"
        f"📛 Tên: {user.get('first_name') or '(chưa rõ)'}\n"
        f"💰 Số dư: <b>{bal:,}đ</b>\n\n"
        f"<i>Khi nạp tiền, hãy đảm bảo nội dung CK có chứa ID "
        f"<code>{tid}</code> ở trên.</i>",
    )


@bot.message_handler(func=lambda m: m.text == "📖 Hướng Dẫn")
def menu_huongdan(m):
    if not _user_allowed(m):
        return
    bot.send_message(
        m.chat.id,
        "📖 <b>HƯỚNG DẪN NẠP TIỀN</b>\n\n"
        "1️⃣ Bấm nút <b>💰 Nạp Tiền</b>.\n"
        "2️⃣ Quét mã QR (đã có sẵn nội dung và tên chủ TK).\n"
        "3️⃣ Nội dung chuyển khoản cần CHỨA ID Telegram của bạn.\n"
        "   Ví dụ: <code>Mua Ao 12345</code>, <code>Doi Tien 12345</code>, "
        "<code>Mua Mi Tom 12345</code>...\n"
        f"4️⃣ Số tiền tối thiểu: <b>{MIN_DEPOSIT:,}đ</b>. Có thể nạp bất kỳ "
        f"số tiền nào ≥ {MIN_DEPOSIT:,}đ.\n"
        "5️⃣ Bot tự động cộng tiền sau <b>10–15 giây</b>.\n\n"
        f"⚠️ Nạp dưới <b>{MIN_DEPOSIT:,}đ</b> → "
        "KHÔNG được cộng, KHÔNG hoàn lại!",
    )


@bot.message_handler(func=lambda m: m.text == "☎️ CSKH")
def menu_cskh(m):
    if not _user_allowed(m):
        return
    bot.send_message(
        m.chat.id,
        "☎️ <b>HỖ TRỢ KHÁCH HÀNG</b>\n\n"
        f"Liên hệ admin: <a href='tg://user?id={ADMIN_ID}'>BẤM VÀO ĐÂY</a>\n"
        f"Hoặc nhắn ID: <code>{ADMIN_ID}</code>",
    )


@bot.message_handler(commands=["huy", "cancel"])
def cmd_huy(m):
    """Huỷ mọi thao tác đang chờ input (cộng/trừ tiền, check user, broadcast…).

    Hoạt động ở mọi trạng thái:
    • Đang chờ input của 1 flow → clear next_step_handler.
    • Không có flow nào → vẫn trả lời, đưa về menu phù hợp.
    """
    chat_id = m.chat.id

    # Xoá mọi next_step_handler đang chờ ở chat này
    try:
        bot.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass

    # Dọn state broadcast nếu có
    BROADCAST_STATE.pop(chat_id, None)

    if is_admin(chat_id):
        bot.send_message(
            chat_id,
            "❎ <b>Đã huỷ thao tác.</b>",
            reply_markup=admin_menu_markup(),
        )
    else:
        if not _user_allowed(m):
            return
        bot.send_message(
            chat_id,
            "❎ <b>Đã huỷ thao tác.</b>",
            reply_markup=main_menu(),
        )


def main_menu_markup():
    """Menu chính cho người dùng"""
    return main_menu()


def shop_menu_markup():
    """Menu SHOP"""
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🌐 Proxy Tĩnh", callback_data="shop_proxy"),
        InlineKeyboardButton("🛒 Shop ACC Tân Thủ", callback_data="shop_acc")
    )
    kb.row(
        InlineKeyboardButton("💳 Data Bank", callback_data="shop_databank")
    )
    kb.row(
        InlineKeyboardButton("🔙 Quay lại", callback_data="shop_back")
    )
    return kb


def send_proxy_menu(telegram_id, edit_message_id=None):
    """Gửi menu mua proxy"""
    price = get_user_price(telegram_id, 'price_proxy_static')
    msg_text = (
        "🌐 <b>MUA PROXY TĨNH (IPV4)</b>\n\n"
        f"💰 Giá: <b>{price:,} VNĐ / 1 Ngày</b>\n\n"
        "👇 <i>Vui lòng chọn nhà cung cấp:</i>"
    )
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🌐 Viettel", callback_data="buy_proxy_select_VIETTEL"),
               InlineKeyboardButton("🌐 FPT", callback_data="buy_proxy_select_FPT"))
    markup.row(InlineKeyboardButton("🌐 VNPT", callback_data="buy_proxy_select_VNPT"))
    markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="shop_back"))
    
    if edit_message_id:
        try:
            bot.edit_message_text(msg_text, telegram_id, edit_message_id, reply_markup=markup)
        except:
            bot.send_message(telegram_id, msg_text, reply_markup=markup)
    else:
        bot.send_message(telegram_id, msg_text, reply_markup=markup)


@bot.message_handler(func=lambda m: m.text in ["👤 Cá Nhân", "💰 Nạp Tiền", "🎁 Thuê Số Ngay", "🛒 SHOP", "🚀 Reg Acc (Chỉ Tạo)", "🤖 Auto Reg + KM", "📖 Hướng Dẫn", "☎️ CSKH"])
def handle_main_menu(m):
    """Handler cho menu chính"""
    cid = m.chat.id
    if is_user_banned(cid):
        return
    
    text = m.text
    
    if text == "👤 Cá Nhân":
        user = get_or_create_user(cid, m.from_user.first_name or "", m.from_user.username or "")
        msg = (
            f"👤 <b>Tài Khoản Của Bạn</b>\n\n"
            f"🆔 ID: <code>{cid}</code>\n"
            f"💰 Số dư hiện tại: <b>{user['balance']:,} VNĐ</b>\n\n"
            f"━━━━━━ 📊 <b>Thống Kê</b> ━━━━━━\n"
            f"💵 Tổng đã nạp: <b>{user['balance']:,} VNĐ</b>\n"
            f"🛒 Tổng đã dùng: <b>0 VNĐ</b>\n\n"
            f"✅ Acc tạo thành công: <b>0</b>\n"
            f"🎁 Acc lên KM thành công: <b>0</b>"
        )
        bot.send_message(cid, msg, reply_markup=main_menu_markup())
        
    elif text == "💰 Nạp Tiền":
        syntax = random.choice(SUGGESTED_SYNTAXES)
        transfer_content = f"{syntax} {cid}"
        qr_url = (
            "https://img.vietqr.io/image/"
            f"{BANK_ACC}-{BANK_NAME}"
            f"-{urllib.parse.quote(transfer_content)}.png"
        )
        caption = (
            f"💰 <b>NẠP TIỀN TỰ ĐỘNG</b>\n\n"
            f"🏦 <b>{BANK_NAME}</b>\n"
            f"👤 <b>{BANK_HOLDER}</b>\n"
            f"📎 <b>STK:</b> <code>{BANK_ACC}</code>\n\n"
            f"📝 <b>Nội dung CK:</b>\n"
            f"<code>{transfer_content}</code>\n\n"
            f"⚠️ <i>Sai nội dung sẽ không được cộng tiền!</i>\n"
            f"💰 <b>Nạp tối thiểu: {MIN_DEPOSIT:,} VNĐ</b>\n"
            f"⏰ <i>Tự động cộng tiền sau 10-15 giây</i>"
        )
        bot.send_photo(cid, qr_url, caption=caption, reply_markup=main_menu_markup())
        
    elif text == "🎁 Thuê Số Ngay":
        if not is_feature_enabled("rent"):
            bot.send_message(cid, "⚠️ Chức năng này đang <b>tạm đóng</b>. Vui lòng quay lại sau!", reply_markup=main_menu_markup())
            return
        bot.send_message(cid, "📱 <b>CHỌN DỊCH VỤ MUỐN THUÊ:</b>\n\n<i>Lưu ý: Bạn chọn dịch vụ nào thì hệ thống sẽ trừ đúng giá tiền của dịch vụ đó.</i>", reply_markup=get_rent_menu(cid))
        
    elif text == "🛒 SHOP":
        bot.send_message(cid, "🛒 <b>SHOP DỊCH VỤ</b>\n\n👇 <i>Chọn dịch vụ bạn muốn:</i>", reply_markup=shop_menu_markup())
        
    elif text == "🚀 Reg Acc (Chỉ Tạo)":
        bot.send_message(cid, "🚀 <b>REG ACC (CHỈ TẠO - KHÔNG XÁC THỰC SĐT)</b>\n💰 Giá: <b>1,234 VNĐ / 1 web</b>\n\nChức năng này đang được phát triển!", reply_markup=main_menu_markup())
        
    elif text == "🤖 Auto Reg + KM":
        bot.send_message(cid, "🤖 <b>HỆ THỐNG AUTO REG + XÁC THỰC + KHUYẾN MÃI</b>\n\nChức năng này đang được phát triển!", reply_markup=main_menu_markup())
        
    elif text == "📖 Hướng Dẫn":
        guide_text = (
            "📖 <b>HƯỚNG DẪN SỬ DỤNG</b>\n\n"
            "━━━━━━ 💰 <b>Nạp Tiền</b> ━━━━━━\n"
            "1. Ấn <b>💰 Nạp Tiền</b>\n"
            "2. Quét mã QR hoặc chuyển khoản\n"
            "3. Đúng nội dung CK để tự động cộng tiền\n\n"
            "━━━━━━ 🎁 <b>Thuê OTP</b> ━━━━━━\n"
            "1. Ấn <b>🎁 Thuê Số Ngay</b>\n"
            "2. Chọn dịch vụ cần thuê\n"
            "3. Chờ OTP được gửi tự động\n\n"
            "━━━━━━ 🛒 <b>SHOP</b> ━━━━━━\n"
            "• <b>Proxy Tĩnh:</b> IP cố định, tốc độ cao\n"
            "• <b>ACC Tân Thủ:</b> Acc đã sẵn sàng, lên KM dễ\n"
            "• <b>Data Bank:</b> Dữ liệu bank thật để đạt KM\n\n"
            "❓ Cần hỗ trợ? Ấn <b>☎️ CSKH</b>"
        )
        bot.send_message(cid, guide_text, reply_markup=main_menu_markup())
        
    elif text == "☎️ CSKH":
        support_text = (
            "☎️ <b>HỖ TRỢ KHÁCH HÀNG</b>\n\n"
            "Bạn cần hỗ trợ hoặc muốn nạp tiền thủ công?\n\n"
            "👤 <b>Admin:</b> @chimdangxem\n"
            "💬 <b>Liên hệ ngay để được tư vấn!</b>\n\n"
            "⏰ <i>Thời gian hỗ trợ: 8:00 - 23:00 hàng ngày</i>"
        )
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("💬 Liên hệ Admin", url="https://t.me/chimdangxem"))
        bot.send_message(cid, support_text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data in ["rent_old_menu", "rent_back"])
def handle_rent_callbacks(call):
    """Handler cho callback thuê OTP"""
    cid = call.from_user.id
    if is_user_banned(cid):
        return
    
    data = call.data
    
    if data == "rent_old_menu":
        bot.edit_message_text(
            "🔄 <b>THUÊ LẠI SỐ CŨ</b>\n\n"
            "Vui lòng chọn dịch vụ bạn muốn thuê lại:",
            chat_id=cid, message_id=call.message.message_id,
            reply_markup=get_rent_old_menu(cid)
        )
    elif data == "rent_back":
        bot.edit_message_text(
            "📱 <b>CHỌN DỊCH VỤ MUỐN THUÊ:</b>\n\n<i>Lưu ý: Bạn chọn dịch vụ nào thì hệ thống sẽ trừ đúng giá tiền của dịch vụ đó.</i>",
            chat_id=cid, message_id=call.message.message_id,
            reply_markup=get_rent_menu(cid)
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith(("rent_", "rentold_", "shop_", "buy_proxy_")))
def handle_service_callbacks(call):
    """Handler cho các dịch vụ"""
    cid = call.from_user.id
    if is_user_banned(cid):
        return
    
    data = call.data
    
    # Xử lý thuê OTP
    if data.startswith("rent_"):
        service_name = data.split("_")[1]
        config = SERVICES.get(service_name)
        if config:
            price = config['price']
            process_rent(cid, service_name, config['id'], price, config['networks'], call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ Dịch vụ không khả dụng.")
    
    elif data.startswith("rentold_"):
        service_name = data.split("_")[1]
        msg = bot.send_message(cid, f"🔄 <b>THUÊ LẠI SỐ CŨ - {service_name}</b>\n\nNhập số điện thoại bạn muốn thuê lại (ví dụ: 0987654321):\n<i>Gõ /huy để hủy bỏ.</i>")
        bot.register_next_step_handler(msg, process_rent_old_step, service_name)
    
    # Xử lý SHOP
    elif data == "shop_proxy":
        send_proxy_menu(cid, call.message.message_id)
    elif data == "shop_acc":
        summary = get_shop_accounts_summary()
        if not summary:
            bot.answer_callback_query(call.id, "😔 Kho ACC đang trống!")
            return
        
        markup = InlineKeyboardMarkup(row_width=2)
        for item in summary:
            site_upper = item['site'].upper()
            count = item['count']
            markup.row(InlineKeyboardButton(f"🎮 {site_upper} ({count} acc)", callback_data=f"shop_buy_acc_{item['site']}"))
        markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="shop_back"))
        
        price = get_user_price(cid, 'price_shopacc')
        total_stock = sum(item['count'] for item in summary)
        msg_text = (
            "🛒 <b>SHOP ACC TÂN THỦ</b>\n\n"
            f"💰 Giá: <b>{price:,} VNĐ / 1 acc</b>\n"
            f"📦 Tổng kho: <b>{total_stock} acc</b>\n\n"
            "📋 <b>Bao gồm:</b> Username | Mật khẩu | Tên acc | Mã PIN rút tiền\n\n"
            "👇 <i>Chọn loại acc bạn muốn mua:</i>"
        )
        try:
            bot.edit_message_text(msg_text, cid, call.message.message_id, reply_markup=markup)
        except:
            bot.send_message(cid, msg_text, reply_markup=markup)
    
    elif data == "shop_databank":
        summary = get_available_data_banks_summary()
        if not summary:
            bot.answer_callback_query(call.id, "😔 Kho Data Bank đang trống!")
            return
        
        markup = InlineKeyboardMarkup(row_width=2)
        for item in summary:
            markup.row(InlineKeyboardButton(f"📁 {item['bank_type']} (Còn {item['count']})", callback_data=f"shop_buy_dbank_{item['bank_type']}"))
        markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="shop_back"))
        
        price = get_user_price(cid, 'price_databank')
        msg_text = (
            "💳 <b>MUA DATA BANK</b>\n\n"
            f"💰 Giá: <b>{price:,} VNĐ / 1 Data Bank</b>\n"
            "📦 <i>Dữ liệu bao gồm: Số tài khoản, Tên Bank, Tên chủ tài khoản.</i>\n\n"
            "Vui lòng chọn loại Bank bạn muốn mua:"
        )
        try:
            bot.edit_message_text(msg_text, cid, call.message.message_id, reply_markup=markup)
        except:
            bot.send_message(cid, msg_text, reply_markup=markup)
    
    elif data == "shop_back":
        bot.send_message(cid, "🛒 <b>SHOP DỊCH VỤ</b>\n\n👇 <i>Chọn dịch vụ bạn muốn:</i>", reply_markup=shop_menu_markup())
    
    # Xử lý mua proxy
    elif data.startswith("buy_proxy_select_"):
        provider = data.replace("buy_proxy_select_", "")
        msg = bot.send_message(cid, f"🌐 <b>MUA PROXY {provider.upper()}</b>\n\n💰 Giá: <b>{get_user_price(cid, 'price_proxy_static'):,} VNĐ / 1 ngày</b>\n\n📝 <b>Nhập số lượng:</b> (ví dụ: 5)\n💡 Gõ /huy để hủy.")
        bot.register_next_step_handler(msg, process_buy_proxy_step, provider)
    
    # Xử lý mua ACC
    elif data.startswith("shop_buy_acc_"):
        site = data.replace("shop_buy_acc_", "")
        stock = get_shop_account_count(site)
        if stock == 0:
            bot.answer_callback_query(call.id, f"❌ {site.upper()} đã hết hàng!", show_alert=True)
            return
        
        price = get_user_price(cid, 'price_shopacc')
        msg = bot.send_message(cid,
            f"🛒 <b>MUA ACC {site.upper()}</b>\n\n"
            f"📦 Kho còn: <b>{stock} acc</b>\n"
            f"💰 Giá: <b>{price:,} VNĐ / acc</b>\n\n"
            f"📝 <b>Nhập số lượng muốn mua</b> (ví dụ: 5)\n"
            f"💡 Gõ /huy để hủy."
        )
        bot.register_next_step_handler(msg, process_buy_acc_step, site)
    
    # Xử lý mua Data Bank
    elif data.startswith("shop_buy_dbank_"):
        bank_type = data.replace("shop_buy_dbank_", "")
        price = get_user_price(cid, 'price_databank')
        msg = bot.send_message(cid, f"📝 <b>NHẬP SỐ LƯỢNG DATA BANK MUỐN MUA</b>\n\n🏦 Loại: <b>{bank_type}</b>\n💰 Giá: <b>{price:,} VNĐ / tài khoản</b>\n\n<i>Nhập một số nguyên (ví dụ: 10). Gõ /huy để hủy bỏ.</i>")
        bot.register_next_step_handler(msg, process_buy_databank_step, bank_type)


def process_buy_proxy_step(message, provider):
    """Xử lý nhập số lượng mua proxy"""
    cid = message.chat.id
    text = message.text.strip()
    
    if text.lower() == "/huy":
        bot.send_message(cid, "✅ Đã hủy.", reply_markup=main_menu_markup())
        return
    
    try:
        amount = int(text)
        if amount <= 0:
            bot.send_message(cid, "❌ Số lượng phải lớn hơn 0!")
            bot.register_next_step_handler(message, process_buy_proxy_step, provider)
            return
    except ValueError:
        bot.send_message(cid, "❌ Vui lòng nhập số nguyên hợp lệ!")
        bot.register_next_step_handler(message, process_buy_proxy_step, provider)
        return
    
    price_per_day = get_user_price(cid, 'price_proxy_static')
    total_cost = price_per_day * amount
    
    user = get_or_create_user(cid)
    if not user or user["balance"] < total_cost:
        bot.send_message(cid, f"❌ Không đủ tiền! Cần <b>{total_cost:,}đ</b> để mua {amount} proxy.\nSố dư: {user['balance']:,} VNĐ.")
        return
    
    # Trừ tiền và mua proxy
    if not deduct_balance(cid, total_cost, "Proxy Static"):
        bot.send_message(cid, "❌ Lỗi trừ tiền, vui lòng thử lại!")
        return
    
    loading_msg = bot.send_message(cid, f"⏳ <i>Đang mua {amount} Proxy {provider}, vui lòng chờ...</i>")
    
    # Gọi API mua proxy
    result = _mua_proxy_vn(loaiproxy=provider, soluong=amount, ngay=1)
    
    if result["status"] == "success":
        proxies = result["proxies"]
        proxy_text = "\n".join([f"<code>{p}</code>" for p in proxies])
        with db_lock:
            c = db_conn()
            cur = c.cursor()
            cur.execute(
                "INSERT INTO proxy_orders(telegram_id, provider, quantity, price, status, proxy_data) VALUES (?,?,?,?,?,?)",
                (cid, provider, len(proxies), total_cost, "completed", "\n".join(proxies)),
            )
            c.commit()
            c.close()
        
        success_text = (
            f"✅ <b>MUA {len(proxies)} PROXY {provider} THÀNH CÔNG!</b>\n\n"
            f"{proxy_text}\n\n"
            f"📅 Thời hạn: <b>1 Ngày</b>\n"
            f"💰 Đã trừ: <b>{total_cost:,} VNĐ</b>\n\n"
            "⚠️ <i>Lưu ý: Hãy lưu lại danh sách này ngay!</i>"
        )
        
        bot.edit_message_text(success_text, cid, loading_msg.message_id, parse_mode='HTML')
    else:
        # Hoàn tiền khi lỗi
        add_balance(cid, total_cost)
        error_msg = result.get("message", "Lỗi không xác định")
        with db_lock:
            c = db_conn()
            cur = c.cursor()
            cur.execute(
                "INSERT INTO proxy_orders(telegram_id, provider, quantity, price, status, proxy_data) VALUES (?,?,?,?,?,?)",
                (cid, provider, amount, total_cost, "failed", error_msg),
            )
            c.commit()
            c.close()
        bot.edit_message_text(f"❌ <b>MUA PROXY THẤT BẠI</b>\n\n⚠️ Lý do: {error_msg}\n🔙 Đã hoàn lại <b>{total_cost:,} VNĐ</b>.", cid, loading_msg.message_id, parse_mode='HTML')


def process_buy_acc_step(message, site):
    """Xử lý mua ACC từ shop"""
    cid = message.chat.id
    text = message.text.strip()
    
    if text.lower() == "/huy":
        bot.send_message(cid, "✅ Đã hủy.", reply_markup=main_menu_markup())
        return
    
    try:
        amount = int(text)
        if amount <= 0:
            bot.send_message(cid, "❌ Số lượng phải lớn hơn 0!")
            bot.register_next_step_handler(message, process_buy_acc_step, site)
            return
    except ValueError:
        bot.send_message(cid, "❌ Vui lòng nhập số nguyên hợp lệ!")
        bot.register_next_step_handler(message, process_buy_acc_step, site)
        return
    
    # Kiểm tra tồn kho
    stock = get_shop_account_count(site)
    if stock < amount:
        bot.send_message(cid, f"❌ Kho <b>{site.upper()}</b> chỉ còn <b>{stock}</b> acc, không đủ <b>{amount}</b> acc!", reply_markup=main_menu_markup())
        return
    
    # Kiểm tra số dư
    price_per_acc = get_user_price(cid, 'price_shopacc')
    total_cost = price_per_acc * amount
    user = get_or_create_user(cid)
    
    if not user or user["balance"] < total_cost:
        current = user['balance'] if user else 0
        bot.send_message(cid,
            f"❌ <b>KHÔNG ĐỦ SỐ DƯ!</b>\n\n"
            f"🛒 Mua: <b>{amount}</b> acc {site.upper()}\n"
            f"💰 Phí: <b>{total_cost:,} VNĐ</b>\n"
            f"💳 Số dư: <b>{current:,} VNĐ</b>\n\nVui lòng nạp thêm!",
            reply_markup=main_menu_markup()
        )
        return
    
    # Trừ tiền và mua acc
    if not deduct_balance(cid, total_cost, "Shop Acc"):
        bot.send_message(cid, "❌ Lỗi trừ tiền!", reply_markup=main_menu_markup())
        return
    
    # Mua acc
    accs = buy_shop_accounts(cid, site, amount)
    if not accs or len(accs) < amount:
        # Hoàn tiền nếu lỗi
        add_balance(cid, total_cost)
        bot.send_message(cid, f"😔 Kho {site.upper()} không đủ số lượng. Đã hoàn lại <b>{total_cost:,} VNĐ</b>!", reply_markup=main_menu_markup())
        return
    
    # Gửi kết quả
    if amount <= 5:
        # Gửi dạng tin nhắn văn bản
        msg_txt = (
            f"✅ <b>MUA THÀNH CÔNG {amount} ACC {site.upper()}!</b>\n\n"
            f"💰 Đã trừ: <b>{total_cost:,} VNĐ</b>\n\n"
        )
        for i, acc in enumerate(accs, 1):
            msg_txt += (
                f"<b>━━━ ACC {i} ━━━</b>\n"
                f"👤 User: <code>{acc['username']}</code>\n"
                f"🔑 Pass: <code>{acc['password']}</code>\n"
                f"📝 Tên: <code>{acc['realname']}</code>\n"
                f"🔢 PIN: <code>{acc['pin']}</code>\n\n"
            )
        msg_txt += "<i>⚠️ Lưu ý: Hãy lưu lại thông tin này ngay!</i>"
        bot.send_message(cid, msg_txt, reply_markup=main_menu_markup())
    else:
        # Gửi dạng file .txt
        import io
        content = f"DANH SÁCH {amount} ACC {site.upper()}\n{'='*50}\n\n"
        for i, acc in enumerate(accs, 1):
            content += f"[ACC {i}]\n"
            content += f"  User: {acc['username']}\n"
            content += f"  Pass: {acc['password']}\n"
            content += f"  Tên:  {acc['realname']}\n"
            content += f"  PIN:  {acc['pin']}\n\n"
        content += f"{'='*50}\nTổng: {amount} acc | Giá: {total_cost:,} VNĐ\n"
        
        file_obj = io.BytesIO(content.encode("utf-8"))
        file_obj.name = f"ShopACC_{site.upper()}_{amount}acc_{int(time.time())}.txt"
        
        bot.send_document(cid, file_obj,
            caption=(
                f"✅ <b>MUA THÀNH CÔNG {amount} ACC {site.upper()}!</b>\n\n"
                f"💰 Đã trừ: <b>{total_cost:,} VNĐ</b>\n"
                f"📦 Chi tiết trong file đính kèm."
            ),
            reply_markup=main_menu_markup()
        )


def process_buy_databank_step(message, bank_type):
    """Xử lý mua Data Bank"""
    cid = message.chat.id
    text = message.text.strip()
    
    if text.lower() == "/huy":
        bot.send_message(cid, "✅ Đã hủy.", reply_markup=main_menu_markup())
        return
    
    try:
        amount = int(text)
        if amount <= 0:
            bot.send_message(cid, "❌ Số lượng phải lớn hơn 0!")
            bot.register_next_step_handler(message, process_buy_databank_step, bank_type)
            return
    except ValueError:
        bot.send_message(cid, "❌ Vui lòng nhập số nguyên hợp lệ!")
        bot.register_next_step_handler(message, process_buy_databank_step, bank_type)
        return
    
    price_per_data = get_user_price(cid, 'price_databank')
    total_cost = price_per_data * amount
    user = get_or_create_user(cid)
    
    if not user or user["balance"] < total_cost:
        bot.send_message(cid, f"❌ Bạn không đủ tiền để mua {amount} Data Bank (Cần {total_cost:,} VNĐ).\nVui lòng nạp thêm tiền!", reply_markup=main_menu_markup())
        return
    
    # Trừ tiền
    if not deduct_balance(cid, total_cost, "Data Bank"):
        bot.send_message(cid, "❌ Lỗi trừ tiền, vui lòng thử lại!", reply_markup=main_menu_markup())
        return
    
    # Mua data bank
    dbanks = buy_multiple_data_banks(cid, bank_type, amount)
    if not dbanks or len(dbanks) < amount:
        # Hoàn tiền nếu lỗi
        add_balance(cid, total_cost)
        bot.send_message(cid, f"😔 Kho Data Bank không đủ số lượng. Đã hoàn lại <b>{total_cost:,} VNĐ</b>!", reply_markup=main_menu_markup())
        return
    
    # Gửi kết quả
    if amount <= 5:
        # Gửi dạng tin nhắn văn bản
        msg_txt = (
            f"✅ <b>MUA THÀNH CÔNG {amount} DATA BANK {bank_type}!</b>\n\n"
            f"💰 Đã trừ: <b>{total_cost:,} VNĐ</b>\n\n"
        )
        for i, dbank in enumerate(dbanks, 1):
            msg_txt += (
                f"<b>━━━ DATA {i} ━━━</b>\n"
                f"🏦 Bank: <code>{bank_type}</code>\n"
                f"📎 STK: <code>{dbank['stk']}</code>\n"
                f"👤 Tên: <code>{dbank['name']}</code>\n\n"
            )
        msg_txt += "<i>⚠️ Lưu ý: Hãy lưu lại thông tin này ngay!</i>"
        bot.send_message(cid, msg_txt, reply_markup=main_menu_markup())
    else:
        # Gửi dạng file .txt
        import io
        content = f"DANH SÁCH {amount} DATA BANK {bank_type}\n{'='*40}\n\n"
        for b in dbanks:
            content += f"STK: {b['stk']} | Tên: {b['name']}\n"
        content += f"\n{'='*40}\nTổng: {amount} data | Giá: {total_cost:,} VNĐ\n"
        
        file_obj = io.BytesIO(content.encode("utf-8"))
        file_obj.name = f"DataBank_{bank_type}_{amount}.txt"
        
        bot.send_document(cid, file_obj,
            caption=(
                f"✅ <b>MUA THÀNH CÔNG {amount} DATA BANK {bank_type}!</b>\n\n"
                f"💰 Đã trừ: <b>{total_cost:,} VNĐ</b>\n"
                f"📦 Chi tiết trong file đính kèm."
            ),
            reply_markup=main_menu_markup()
        )


# ================== ADMIN ==================
def is_admin(tid):
    return tid == ADMIN_ID


def admin_menu_markup():
    """Inline keyboard chính cho admin panel."""
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("🔍 Check User", callback_data="adm_check"),
        InlineKeyboardButton("📊 Thống Kê", callback_data="adm_stats"),
    )
    kb.row(
        InlineKeyboardButton("📜 Giao Dịch", callback_data="adm_recent"),
        InlineKeyboardButton("👥 Top User", callback_data="adm_topuser"),
    )
    kb.row(
        InlineKeyboardButton("💵 Chỉnh Giá", callback_data="adm_prices"),
        InlineKeyboardButton("🛒 Quản Lý Shop", callback_data="adm_shop"),
    )
    kb.row(
        InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
        InlineKeyboardButton("❌ Đóng", callback_data="adm_close"),
    )
    return kb


def back_to_admin_markup():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 Quay lại Admin", callback_data="adm_main"))
    return kb


@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if not is_admin(m.chat.id):
        return
    bot.send_message(
        m.chat.id,
        "🔧 <b>ADMIN PANEL</b>\n\nChọn chức năng cần thao tác 👇",
        reply_markup=admin_menu_markup(),
    )


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("adm_"))
def handle_admin_cb(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Không có quyền")
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    data = call.data
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if data == "adm_main":
        try:
            bot.edit_message_text(
                "🔧 <b>ADMIN PANEL</b>\n\nChọn chức năng cần thao tác 👇",
                chat_id, msg_id,
                reply_markup=admin_menu_markup(),
                parse_mode="HTML",
            )
        except Exception:
            bot.send_message(chat_id,
                "🔧 <b>ADMIN PANEL</b>\n\nChọn chức năng cần thao tác 👇",
                reply_markup=admin_menu_markup())
    elif data == "adm_close":
        try:
            bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
    elif data == "adm_stats":
        show_stats(chat_id, msg_id)
    elif data == "adm_recent":
        show_recent_deposits(chat_id, msg_id)
    elif data == "adm_topuser":
        show_top_users(chat_id, msg_id)
    elif data == "adm_prices":
        show_price_settings(chat_id, msg_id)
    elif data.startswith("adm_price_"):
        ask_set_price(chat_id, msg_id, data.replace("adm_price_", "", 1))
    elif data == "adm_check":
        ask_input_check(chat_id, msg_id)
    elif data == "adm_shop":
        show_shop_management(chat_id, msg_id)
    elif data == "adm_broadcast":
        ask_broadcast_text(chat_id, msg_id)
    elif data == "adm_broadcast_send":
        do_broadcast_send(call)
    elif data.startswith("adm_shop_"):
        handle_shop_management(call, data.replace("adm_shop_", "", 1))


def _safe_edit(chat_id, msg_id, text, markup=None):
    try:
        bot.edit_message_text(text, chat_id, msg_id,
                              reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(chat_id, text, reply_markup=markup)


def show_shop_management(chat_id, msg_id):
    """Hiển thị menu quản lý shop"""
    # Lấy thống kê kho
    acc_summary = get_shop_accounts_summary()
    dbank_summary = get_available_data_banks_summary()
    
    acc_text = "\n".join([f"  🎮 {s['site'].upper()}: <b>{s['count']}</b> acc" for s in acc_summary]) if acc_summary else "  <i>Trống</i>"
    dbank_text = "\n".join([f"  📁 {b['bank_type']}: <b>{b['count']}</b> data" for b in dbank_summary]) if dbank_summary else "  <i>Trống</i>"
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("➕ Thêm ACC", callback_data="adm_shop_add_acc"),
        InlineKeyboardButton("➕ Thêm Data Bank", callback_data="adm_shop_add_dbank")
    )
    markup.row(
        InlineKeyboardButton("📋 Xem Kho ACC", callback_data="adm_shop_view_acc"),
        InlineKeyboardButton("📋 Xem Kho Data Bank", callback_data="adm_shop_view_dbank")
    )
    markup.row(
        InlineKeyboardButton("🗑️ Xóa Sản Phẩm", callback_data="adm_shop_delete"),
        InlineKeyboardButton("🔙 Quay lại", callback_data="adm_main")
    )
    
    text = (
        f"🛒 <b>QUẢN LÝ SHOP</b>\n\n"
        f"📊 <b>Thống Kê Kho:</b>\n\n"
        f"🎮 <b>ACC TÂN THỦ:</b>\n{acc_text}\n\n"
        f"💳 <b>DATA BANK:</b>\n{dbank_text}\n\n"
        f"👇 <i>Chọn chức năng quản lý:</i>"
    )
    
    _safe_edit(chat_id, msg_id, text, markup)


def handle_shop_management(call, action):
    """Xử lý các action quản lý shop"""
    chat_id = call.from_user.id
    msg_id = call.message.message_id
    
    if action == "add_acc":
        markup = InlineKeyboardMarkup()
        for site in ["f168", "c168", "cm88", "sc88", "fly88"]:
            markup.row(InlineKeyboardButton(f"➕ Thêm ACC {site.upper()}", callback_data=f"adm_shop_add_acc_{site}"))
        markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
        
        text = "🛒 <b>THÊM ACC TÂN THỦ</b>\n\nChọn loại acc muốn thêm:"
        _safe_edit(chat_id, msg_id, text, markup)
        
    elif action.startswith("add_acc_"):
        site = action.replace("add_acc_", "")
        msg = bot.send_message(chat_id, 
            f"📝 <b>NHẬP ACC {site.upper()}</b>\n\n"
            f"Định dạng: <code>username|password|realname|pin</code>\n"
            f"Mỗi dòng 1 acc, pin mặc định là 111222 nếu không nhập\n\n"
            f"Ví dụ:\n"
            f"<code>user123|pass456|NGUYEN VAN A|111222</code>\n"
            f"<code>user789|pass012|TRAN VAN B</code>\n\n"
            f"Gửi file hoặc nhập trực tiếp. Gõ /huy để hủy."
        )
        bot.register_next_step_handler(msg, process_add_shop_acc, site)
        
    elif action == "add_dbank":
        msg = bot.send_message(chat_id,
            "📝 <b>NHẬP DATA BANK</b>\n\n"
            "Định dạng: <code>bank_type|stk|name</code>\n"
            "Mỗi dòng 1 data\n\n"
            "Ví dụ:\n"
            "<code>VCB|123456789|NGUYEN VAN A</code>\n"
            "<code>TCB|987654321|TRAN VAN B</code>\n\n"
            "Gửi file hoặc nhập trực tiếp. Gõ /huy để hủy."
        )
        bot.register_next_step_handler(msg, process_add_data_bank)
        
    elif action == "view_acc":
        show_shop_inventory(chat_id, msg_id, "acc")
        
    elif action == "view_dbank":
        show_shop_inventory(chat_id, msg_id, "dbank")
        
    elif action.startswith("acc_detail_"):
        site = action.replace("acc_detail_", "")
        show_acc_detail(chat_id, msg_id, site)
        
    elif action.startswith("dbank_detail_"):
        bank_type = action.replace("dbank_detail_", "")
        show_dbank_detail(chat_id, msg_id, bank_type)
        
    elif action == "delete_acc":
        msg = bot.send_message(chat_id,
            "🗑️ <b>XÓA ACC</b>\n\nNhập username acc cần xóa (mỗi dòng 1 username):"
        )
        bot.register_next_step_handler(msg, process_delete_acc)
        
    elif action == "delete_dbank":
        msg = bot.send_message(chat_id,
            "🗑️ <b>XÓA DATA BANK</b>\n\nNhập STK data bank cần xóa (mỗi dòng 1 STK):"
        )
        bot.register_next_step_handler(msg, process_delete_dbank)
        
    elif action == "delete":
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🗑️ Xóa ACC", callback_data="adm_shop_delete_acc"),
            InlineKeyboardButton("🗑️ Xóa Data Bank", callback_data="adm_shop_delete_dbank")
        )
        markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
        
        text = "🗑️ <b>XÓA SẢN PHẨM</b>\n\nChọn loại sản phẩm muốn xóa:"
        _safe_edit(chat_id, msg_id, text, markup)


def process_add_shop_acc(message, site):
    """Xử lý thêm acc shop"""
    chat_id = message.chat.id
    text = ""
    
    if message.text and message.text.strip().lower() == "/huy":
        bot.send_message(chat_id, "✅ Đã hủy thêm acc.", reply_markup=back_to_admin_markup())
        return
    
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Lỗi đọc file: {e}")
            return
    elif message.text:
        text = message.text.strip()
    
    if not text:
        bot.send_message(chat_id, "❌ Không tìm thấy dữ liệu!")
        return
    
    lines = text.split('\n')
    accounts = []
    errors = 0
    
    for line in lines:
        line = line.strip()
        if not line: continue
        parts = [p.strip() for p in line.split('|')]
        
        if len(parts) >= 2:
            username = parts[0]
            password = parts[1]
            realname = parts[2] if len(parts) >= 3 else ""
            pin = parts[3] if len(parts) >= 4 else "111222"
            accounts.append({
                "site": site,
                "username": username,
                "password": password,
                "realname": realname,
                "pin": pin
            })
        else:
            errors += 1
    
    if accounts:
        add_shop_accounts_bulk(accounts)
    
    bot.send_message(chat_id,
        f"✅ <b>HOÀN TẤT THÊM ACC {site.upper()}</b>\n\n"
        f"✅ Thêm thành công: <b>{len(accounts)}</b>\n"
        f"❌ Lỗi định dạng: <b>{errors}</b>",
        reply_markup=back_to_admin_markup()
    )


def process_add_data_bank(message):
    """Xử lý thêm data bank"""
    chat_id = message.chat.id
    text = ""
    
    if message.text and message.text.strip().lower() == "/huy":
        bot.send_message(chat_id, "✅ Đã hủy thêm data bank.", reply_markup=back_to_admin_markup())
        return
    
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Lỗi đọc file: {e}")
            return
    elif message.text:
        text = message.text.strip()
    
    if not text:
        bot.send_message(chat_id, "❌ Không tìm thấy dữ liệu!")
        return
    
    lines = text.split('\n')
    added = 0
    errors = 0
    
    for line in lines:
        line = line.strip()
        if not line: continue
        parts = [p.strip() for p in line.split('|')]
        
        if len(parts) >= 3:
            bank_type = parts[0]
            stk = parts[1]
            name = parts[2]
            add_data_bank(bank_type, stk, name)
            added += 1
        else:
            errors += 1
    
    bot.send_message(chat_id,
        f"✅ <b>HOÀN TẤT THÊM DATA BANK</b>\n\n"
        f"✅ Thêm thành công: <b>{added}</b>\n"
        f"❌ Lỗi định dạng: <b>{errors}</b>",
        reply_markup=back_to_admin_markup()
    )


def show_shop_inventory(chat_id, msg_id, inventory_type):
    """Hiển thị kho hàng"""
    markup = InlineKeyboardMarkup()
    
    if inventory_type == "acc":
        summary = get_shop_accounts_summary()
        if not summary:
            text = "📋 <b>KHO ACC TRỐNG</b>\n\nKhông có acc nào trong kho."
            markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
        else:
            buttons = []
            for item in summary:
                buttons.append(InlineKeyboardButton(f"🎮 {item['site'].upper()} ({item['count']})", callback_data=f"adm_shop_acc_detail_{item['site']}"))
            markup.add(*buttons)
            markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
            
            text = "📋 <b>KHO ACC TÂN THỦ</b>\n\n" + "\n".join([f"🎮 {s['site'].upper()}: {s['count']} acc" for s in summary])
    
    else:  # dbank
        summary = get_available_data_banks_summary()
        if not summary:
            text = "📋 <b>KHO DATA BANK TRỐNG</b>\n\nKhông có data bank nào trong kho."
            markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
        else:
            buttons = []
            for item in summary:
                buttons.append(InlineKeyboardButton(f"📁 {item['bank_type']} ({item['count']})", callback_data=f"adm_shop_dbank_detail_{item['bank_type']}"))
            markup.add(*buttons)
            markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop"))
            
            text = "📋 <b>KHO DATA BANK</b>\n\n" + "\n".join([f"📁 {b['bank_type']}: {b['count']} data" for b in summary])
    
    _safe_edit(chat_id, msg_id, text, markup)


def get_shop_accounts_summary():
    """Lấy thống kê kho acc"""
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            cur.execute("""
                SELECT site, COUNT(*) as count 
                FROM shop_accounts 
                WHERE is_sold = 0 
                GROUP BY site 
                ORDER BY site
            """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"Error getting shop accounts summary: {e}")
        return []


def get_available_data_banks_summary():
    """Lấy thống kê kho data bank"""
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            cur.execute("""
                SELECT bank_type, COUNT(*) as count 
                FROM data_banks 
                WHERE is_sold = 0 
                GROUP BY bank_type 
                ORDER BY bank_type
            """)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"Error getting data banks summary: {e}")
        return []


def add_shop_accounts_bulk(accounts):
    """Thêm nhiều acc vào kho"""
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            for acc in accounts:
                cur.execute("""
                    INSERT INTO shop_accounts 
                    (site, username, password, realname, pin, is_sold, created_at)
                    VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                """, (acc['site'], acc['username'], acc['password'], acc['realname'], acc['pin']))
            c.commit()
    except Exception as e:
        print(f"Error adding shop accounts: {e}")


def back_to_admin_markup():
    """Tạo nút quay lại admin panel"""
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("🔙 Quay lại Admin", callback_data="adm_main")
    )


def show_acc_detail(chat_id, msg_id, site):
    """Hiển thị chi tiết acc theo site"""
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            cur.execute("""
                SELECT id, username, password, realname, pin, created_at
                FROM shop_accounts 
                WHERE site = ? AND is_sold = 0 
                ORDER BY created_at DESC
                LIMIT 20
            """, (site,))
            
            accounts = cur.fetchall()
            
            if not accounts:
                text = f"📋 <b>KHO ACC {site.upper()} TRỐNG</b>\n\nKhông có acc nào trong kho."
                markup = InlineKeyboardMarkup().row(
                    InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop_view_acc")
                )
            else:
                text = f"📋 <b>KHO ACC {site.upper()}</b>\n\n"
                for i, acc in enumerate(accounts, 1):
                    text += f"{i}. <code>{acc['username']}</code> | {acc['realname'] or 'N/A'} | PIN: {acc['pin']}\n"
                
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🔄 Làm mới", callback_data=f"adm_shop_acc_detail_{site}"))
                markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop_view_acc"))
            
            _safe_edit(chat_id, msg_id, text, markup)
            
    except Exception as e:
        _safe_edit(chat_id, msg_id, f"❌ Lỗi tải dữ liệu: {e}")


def show_dbank_detail(chat_id, msg_id, bank_type):
    """Hiển thị chi tiết data bank theo loại"""
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            cur.execute("""
                SELECT id, stk, name, created_at
                FROM data_banks 
                WHERE bank_type = ? AND is_sold = 0 
                ORDER BY created_at DESC
                LIMIT 20
            """, (bank_type,))
            
            data_banks = cur.fetchall()
            
            if not data_banks:
                text = f"📋 <b>KHO DATA BANK {bank_type} TRỐNG</b>\n\nKhông có data nào trong kho."
                markup = InlineKeyboardMarkup().row(
                    InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop_view_dbank")
                )
            else:
                text = f"📋 <b>KHO DATA BANK {bank_type}</b>\n\n"
                for i, data in enumerate(data_banks, 1):
                    text += f"{i}. <code>{data['stk']}</code> | {data['name']}\n"
                
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🔄 Làm mới", callback_data=f"adm_shop_dbank_detail_{bank_type}"))
                markup.row(InlineKeyboardButton("🔙 Quay lại", callback_data="adm_shop_view_dbank"))
            
            _safe_edit(chat_id, msg_id, text, markup)
            
    except Exception as e:
        _safe_edit(chat_id, msg_id, f"❌ Lỗi tải dữ liệu: {e}")


def process_delete_acc(message):
    """Xử lý xóa acc"""
    chat_id = message.chat.id
    text = ""
    
    if message.text and message.text.strip().lower() == "/huy":
        bot.send_message(chat_id, "✅ Đã hủy xóa acc.", reply_markup=back_to_admin_markup())
        return
    
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Lỗi đọc file: {e}")
            return
    elif message.text:
        text = message.text.strip()
    
    if not text:
        bot.send_message(chat_id, "❌ Không tìm thấy dữ liệu!")
        return
    
    lines = text.split('\n')
    deleted = 0
    not_found = 0
    
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            for line in lines:
                username = line.strip()
                if not username: continue
                
                cur.execute("DELETE FROM shop_accounts WHERE username = ? AND is_sold = 0", (username,))
                if cur.rowcount > 0:
                    deleted += 1
                else:
                    not_found += 1
            c.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ Lỗi xóa acc: {e}")
        return
    
    bot.send_message(chat_id,
        f"✅ <b>HOÀN TẤT XÓA ACC</b>\n\n"
        f"✅ Đã xóa: <b>{deleted}</b>\n"
        f"❌ Không tìm thấy: <b>{not_found}</b>",
        reply_markup=back_to_admin_markup()
    )


def process_delete_dbank(message):
    """Xử lý xóa data bank"""
    chat_id = message.chat.id
    text = ""
    
    if message.text and message.text.strip().lower() == "/huy":
        bot.send_message(chat_id, "✅ Đã hủy xóa data bank.", reply_markup=back_to_admin_markup())
        return
    
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(chat_id, f"❌ Lỗi đọc file: {e}")
            return
    elif message.text:
        text = message.text.strip()
    
    if not text:
        bot.send_message(chat_id, "❌ Không tìm thấy dữ liệu!")
        return
    
    lines = text.split('\n')
    deleted = 0
    not_found = 0
    
    try:
        with db_lock:
            c = db_conn(); cur = c.cursor()
            for line in lines:
                stk = line.strip()
                if not stk: continue
                
                cur.execute("DELETE FROM data_banks WHERE stk = ? AND is_sold = 0", (stk,))
                if cur.rowcount > 0:
                    deleted += 1
                else:
                    not_found += 1
            c.commit()
    except Exception as e:
        bot.send_message(chat_id, f"❌ Lỗi xóa data bank: {e}")
        return
    
    bot.send_message(chat_id,
        f"✅ <b>HOÀN TẤT XÓA DATA BANK</b>\n\n"
        f"✅ Đã xóa: <b>{deleted}</b>\n"
        f"❌ Không tìm thấy: <b>{not_found}</b>",
        reply_markup=back_to_admin_markup()
    )


# ----- THỐNG KÊ -----
def show_stats(chat_id, msg_id):
    with db_lock:
        c = db_conn(); cur = c.cursor()
        
        # Thống kê users và balance
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()["cnt"]
        cur.execute("SELECT IFNULL(SUM(balance), 0) AS s FROM users")
        total_balance = cur.fetchone()["s"]
        
        # Thống kê deposits
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS s "
            "FROM deposits WHERE status='success'"
        )
        r = cur.fetchone(); ok_cnt, ok_sum = r["cnt"], r["s"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS s "
            "FROM deposits WHERE status='rejected_low_amount'"
        )
        r = cur.fetchone(); rj_cnt, rj_sum = r["cnt"], r["s"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS s "
            "FROM deposits WHERE status='success' "
            "AND date(created_at) = date('now', 'localtime')"
        )
        r = cur.fetchone(); td_cnt, td_sum = r["cnt"], r["s"]
        
        # Thống kê OTP
        cur.execute("SELECT COUNT(*) AS cnt FROM rent_history")
        total_otp_rents = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(price),0) AS s "
            "FROM rent_history WHERE status IN ('success', 'completed')"
        )
        r = cur.fetchone(); otp_success_cnt, otp_revenue = r["cnt"], r["s"]
        cur.execute(
            "SELECT COUNT(*) AS cnt "
            "FROM rent_history WHERE date(rented_at) = date('now', 'localtime')"
        )
        today_otp = cur.fetchone()["cnt"]
        
        # Thống kê SHOP - Proxy
        cur.execute("SELECT COUNT(*) AS cnt FROM proxy_orders")
        total_proxy_orders = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(price),0) AS s "
            "FROM proxy_orders WHERE status='completed'"
        )
        r = cur.fetchone(); proxy_success_cnt, proxy_revenue = r["cnt"], r["s"]
        
        # Thống kê SHOP - ACC
        cur.execute("SELECT COUNT(*) AS cnt FROM shop_accounts WHERE is_sold = 0")
        acc_available = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM shop_accounts WHERE is_sold = 1")
        acc_sold = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(price),0) AS s "
            "FROM shop_accounts WHERE is_sold = 1"
        )
        r = cur.fetchone(); acc_sold_cnt, acc_revenue = r["cnt"], r["s"]
        
        # Thống kê SHOP - Data Bank
        cur.execute("SELECT COUNT(*) AS cnt FROM data_banks WHERE is_sold = 0")
        dbank_available = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM data_banks WHERE is_sold = 1")
        dbank_sold = cur.fetchone()["cnt"]
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(price),0) AS s "
            "FROM data_banks WHERE is_sold = 1"
        )
        r = cur.fetchone(); dbank_sold_cnt, dbank_revenue = r["cnt"], r["s"]
        
        c.close()

    # Tính tổng doanh thu
    total_revenue = otp_revenue + proxy_revenue + acc_revenue + dbank_revenue

    text = (
        "📊 <b>THỐNG KÊ NEWBOT</b>\n\n"
        f"👥 Tổng user: <b>{total_users:,}</b>\n"
        f"💰 Tổng số dư đang giữ: <b>{total_balance:,}đ</b>\n\n"
        
        f"━━━ <b>GIAO DỊCH NẠP TIỀN</b> ━━━\n"
        f"✅ Thành công: <b>{ok_cnt:,}</b> giao dịch / <b>{ok_sum:,}đ</b>\n"
        f"❌ Dưới hạn mức: <b>{rj_cnt:,}</b> giao dịch / <b>{rj_sum:,}đ</b>\n"
        f"📅 Hôm nay: <b>{td_cnt:,}</b> / <b>{td_sum:,}đ</b>\n\n"
        
        f"━━━ <b>DỊCH VỤ OTP</b> ━━━\n"
        f"🔢 Tổng lượt thuê: <b>{total_otp_rents:,}</b>\n"
        f"✅ Hoàn thành: <b>{otp_success_cnt:,}</b>\n"
        f"💸 Doanh thu OTP: <b>{otp_revenue:,}đ</b>\n"
        f"📅 Hôm nay: <b>{today_otp:,}</b> lượt\n\n"
        
        f"━━━ <b>SHOP PROXY</b> ━━━\n"
        f"📡 Tổng đơn: <b>{total_proxy_orders:,}</b>\n"
        f"✅ Hoàn thành: <b>{proxy_success_cnt:,}</b>\n"
        f"💸 Doanh thu Proxy: <b>{proxy_revenue:,}đ</b>\n\n"
        
        f"━━━ <b>SHOP ACC TÂN THỦ</b> ━━━\n"
        f"🎮 Còn kho: <b>{acc_available:,}</b>\n"
        f"✅ Đã bán: <b>{acc_sold:,}</b>\n"
        f"💸 Doanh thu ACC: <b>{acc_revenue:,}đ</b>\n\n"
        
        f"━━━ <b>SHOP DATA BANK</b> ━━━\n"
        f"💳 Còn kho: <b>{dbank_available:,}</b>\n"
        f"✅ Đã bán: <b>{dbank_sold:,}</b>\n"
        f"💸 Doanh thu Data Bank: <b>{dbank_revenue:,}đ</b>\n\n"
        
        f"━━━ <b>TỔNG DOANH THU</b> ━━━\n"
        f"💰 <b>{total_revenue:,}đ</b>"
    )
    _safe_edit(chat_id, msg_id, text, back_to_admin_markup())


def show_recent_deposits(chat_id, msg_id):
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute(
            "SELECT d.*, u.username, u.first_name "
            "FROM deposits d LEFT JOIN users u ON d.telegram_id = u.telegram_id "
            "ORDER BY d.id DESC LIMIT 20"
        )
        rows = [dict(r) for r in cur.fetchall()]
        c.close()
    if not rows:
        text = "📭 <b>GIAO DỊCH</b>\n\nChưa có giao dịch nào."
    else:
        lines = ["💰 <b>20 GIAO DỊCH GẦN NHẤT</b>\n"]
        for r in rows:
            flag = "✅" if r["status"] == "success" else "❌"
            ct = (r["content"] or "")[:30]
            uname = r.get("username") or ""
            label = f"@{uname}" if uname else (r.get("first_name") or "")
            label = (label[:14] + "…") if len(label) > 14 else label
            lines.append(
                f"{flag} <code>{r['telegram_id']}</code> {label} | "
                f"<b>{r['amount']:,}đ</b> | <i>{ct}</i>"
            )
        text = "\n".join(lines)
    _safe_edit(chat_id, msg_id, text, back_to_admin_markup())


def show_top_users(chat_id, msg_id):
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute("SELECT * FROM users ORDER BY balance DESC LIMIT 20")
        rows = [dict(r) for r in cur.fetchall()]
        c.close()
    if not rows:
        text = "📭 <b>TOP USER</b>\n\nChưa có user nào."
    else:
        lines = ["👥 <b>TOP 20 USER (theo số dư)</b>\n"]
        for i, r in enumerate(rows, 1):
            label = r.get("first_name") or r.get("username") or ""
            label = (label[:18] + "…") if len(label) > 18 else label
            lines.append(
                f"{i}. <code>{r['telegram_id']}</code> "
                f"{label} — <b>{r['balance']:,}đ</b>"
            )
        text = "\n".join(lines)
    _safe_edit(chat_id, msg_id, text, back_to_admin_markup())


# ----- CHỈNH GIÁ SẢN PHẨM -----
def price_settings_markup():
    kb = InlineKeyboardMarkup()
    for key, label, _default in PRICE_SETTINGS:
        kb.row(InlineKeyboardButton(
            f"✏️ {label}",
            callback_data=f"adm_price_{key}",
        ))
    kb.row(InlineKeyboardButton("🔙 Quay lại Admin", callback_data="adm_main"))
    return kb


def show_price_settings(chat_id, msg_id=None):
    lines = ["💵 <b>CHỈNH GIÁ SẢN PHẨM</b>", ""]
    for key, label, _default in PRICE_SETTINGS:
        lines.append(f"• <b>{label}</b>: <code>{get_price(key):,}</code>đ")
    lines.extend([
        "",
        "Bấm vào sản phẩm để đổi giá.",
        "Nhập <code>0</code> nếu muốn tạm miễn phí sản phẩm đó.",
    ])
    text = "\n".join(lines)
    if msg_id:
        _safe_edit(chat_id, msg_id, text, price_settings_markup())
    else:
        bot.send_message(chat_id, text, reply_markup=price_settings_markup())


def ask_set_price(chat_id, msg_id, key):
    if key not in PRICE_LABELS:
        _safe_edit(chat_id, msg_id, "❌ Không tìm thấy sản phẩm.", back_to_admin_markup())
        return
    _safe_edit(
        chat_id, msg_id,
        f"💵 <b>ĐỔI GIÁ</b>\n\n"
        f"Sản phẩm: <b>{PRICE_LABELS[key]}</b>\n"
        f"Giá hiện tại: <code>{get_price(key):,}</code>đ\n\n"
        "Gửi giá mới bằng số nguyên VNĐ.\n"
        "Ví dụ: <code>1234</code>\n\n"
        "Hoặc gõ /huy để huỷ.",
        back_to_admin_markup(),
    )
    bot.register_next_step_handler_by_chat_id(
        chat_id,
        lambda m: process_set_price(m, key),
    )


def process_set_price(message, key):
    if not is_admin(message.chat.id):
        return
    text = (message.text or "").strip()
    if text.startswith("/"):
        bot.send_message(message.chat.id, "❎ Đã huỷ.", reply_markup=admin_menu_markup())
        return
    try:
        price = int(text.replace(",", "").replace(".", ""))
        if price < 0:
            raise ValueError("price < 0")
    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Giá không hợp lệ. Hãy nhập số nguyên VNĐ, ví dụ <code>1234</code>.",
            reply_markup=admin_menu_markup(),
        )
        return
    set_setting(key, str(price))
    bot.send_message(
        message.chat.id,
        f"✅ Đã cập nhật <b>{PRICE_LABELS.get(key, key)}</b> = "
        f"<code>{price:,}</code>đ.",
        reply_markup=admin_menu_markup(),
    )


# ----- CHECK USER + FULL INFO + INLINE ACTIONS -----
def ask_input_check(chat_id, msg_id):
    _safe_edit(
        chat_id, msg_id,
        "🔍 <b>CHECK USER</b>\n\n"
        "Gõ ID user cần kiểm tra:\n"
        "Ví dụ: <code>5724397112</code>\n\n"
        "Hoặc gõ /huy để huỷ.",
        back_to_admin_markup(),
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_check)


def process_check(message):
    if not is_admin(message.chat.id):
        return
    if (message.text or "").strip().startswith("/"):
        bot.send_message(message.chat.id, "❎ Đã huỷ.", reply_markup=admin_menu_markup())
        return
    try:
        tid = int(message.text.strip())
        show_user_full_info(message.chat.id, None, tid)
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ ID không hợp lệ ({e}).",
            reply_markup=admin_menu_markup(),
        )


def user_action_markup(tid, is_banned):
    """Inline keyboard cho 1 user trong panel Check User."""
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("➕ Cộng Tiền", callback_data=f"chk_cong_{tid}"),
        InlineKeyboardButton("➖ Trừ Tiền", callback_data=f"chk_tru_{tid}"),
    )
    if is_banned:
        kb.row(
            InlineKeyboardButton("✅ Mở Chặn", callback_data=f"chk_unban_{tid}"),
            InlineKeyboardButton("🔄 Làm mới", callback_data=f"chk_refresh_{tid}"),
        )
    else:
        kb.row(
            InlineKeyboardButton("🚫 Chặn User", callback_data=f"chk_ban_{tid}"),
            InlineKeyboardButton("🔄 Làm mới", callback_data=f"chk_refresh_{tid}"),
        )
    kb.row(InlineKeyboardButton("🔙 Quay lại Admin", callback_data="adm_main"))
    return kb


def show_user_full_info(chat_id, msg_id, tid):
    """Hiện full thông tin 1 user + inline buttons để Cộng/Trừ/Chặn.

    Nếu msg_id is not None → edit tin nhắn cũ, ngược lại gửi tin mới.
    """
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
        u = cur.fetchone()
        u = dict(u) if u else None
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS s "
            "FROM deposits WHERE telegram_id=? AND status='success'",
            (tid,),
        )
        ok_stats = dict(cur.fetchone())
        cur.execute(
            "SELECT COUNT(*) AS cnt, IFNULL(SUM(amount),0) AS s "
            "FROM deposits WHERE telegram_id=? AND status='rejected_low_amount'",
            (tid,),
        )
        rj_stats = dict(cur.fetchone())
        cur.execute(
            "SELECT * FROM deposits WHERE telegram_id=? "
            "ORDER BY id DESC LIMIT 5",
            (tid,),
        )
        recent_deposits = [dict(r) for r in cur.fetchall()]
        c.close()
    expenses_breakdown, expenses_total = get_user_expenses_breakdown(tid)

    if not u:
        text = f"❌ Không tìm thấy user <code>{tid}</code>."
        markup = back_to_admin_markup()
    else:
        is_banned = bool(u.get("is_banned"))
        ban_emoji = "🚫" if is_banned else "✅"
        ban_state = "ĐÃ BỊ CHẶN" if is_banned else "Bình thường"

        lines = [
            f"🔍 <b>USER {tid}</b> {ban_emoji}",
            "",
            f"📛 Tên: {u.get('first_name') or '(trống)'}",
            f"@ Username: @{u.get('username') or '(trống)'}",
            f"📅 Tham gia: <i>{u['created_at']}</i>",
            f"🛡️ Trạng thái: <b>{ban_state}</b>",
            "",
            f"💰 <b>SỐ DƯ HIỆN TẠI:</b> <b>{u['balance']:,}đ</b>",
            "",
            "━━━ <b>NẠP TIỀN</b> ━━━",
            f"✅ Tổng nạp: <b>{ok_stats['cnt']:,}</b> lần / <b>{ok_stats['s']:,}đ</b>",
            f"❌ Dưới hạn mức: <b>{rj_stats['cnt']:,}</b> lần / <b>{rj_stats['s']:,}đ</b>",
        ]
        # Lịch sử 5 lần nạp gần nhất
        if recent_deposits:
            lines.append("")
            lines.append("📜 <b>5 lần nạp gần nhất:</b>")
            for r in recent_deposits:
                flag = "✅" if r["status"] == "success" else "❌"
                ct = (r["content"] or "")[:25]
                ts = (r["created_at"] or "")[:16]
                lines.append(
                    f"   {flag} <b>{r['amount']:,}đ</b> | <i>{ct}</i> | {ts}"
                )

        # Đã chi tiêu cho dịch vụ nào
        lines.append("")
        lines.append("━━━ <b>ĐÃ DÙNG TIỀN</b> ━━━")
        if expenses_total["cnt"] > 0:
            lines.append(
                f"💳 Tổng chi: <b>{expenses_total['total']:,}đ</b> "
                f"({expenses_total['cnt']:,} lần)"
            )
            for e in expenses_breakdown:
                lines.append(
                    f"   • {e['service_type']}: "
                    f"<b>{e['total']:,}đ</b> ({e['cnt']:,} lần)"
                )
        else:
            lines.append("<i>Chưa dùng dịch vụ nào.</i>")

        text = "\n".join(lines)
        markup = user_action_markup(tid, is_banned)

    if msg_id:
        _safe_edit(chat_id, msg_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("chk_"))
def handle_check_cb(call):
    if not is_admin(call.from_user.id):
        try:
            bot.answer_callback_query(call.id, "⛔ Không có quyền")
        except Exception:
            pass
        return
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    # Format: chk_<action>_<tid>
    parts = call.data.split("_", 2)
    if len(parts) < 3:
        return
    action = parts[1]
    try:
        tid = int(parts[2])
    except Exception:
        return

    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if action == "refresh":
        show_user_full_info(chat_id, msg_id, tid)
    elif action == "ban":
        get_or_create_user(tid)
        set_user_banned(tid, True)
        try:
            bot.send_message(
                tid,
                "🚫 <b>TÀI KHOẢN CỦA BẠN ĐÃ BỊ CHẶN</b>\n\n"
                "Bạn không thể tiếp tục sử dụng bot.\n"
                f"Liên hệ admin: <a href='tg://user?id={ADMIN_ID}'>BẤM VÀO ĐÂY</a>",
            )
        except Exception:
            pass
        show_user_full_info(chat_id, msg_id, tid)
    elif action == "unban":
        get_or_create_user(tid)
        set_user_banned(tid, False)
        try:
            bot.send_message(
                tid,
                "✅ <b>TÀI KHOẢN ĐÃ ĐƯỢC MỞ</b>\n\n"
                "Bạn có thể tiếp tục sử dụng bot bình thường.",
            )
        except Exception:
            pass
        show_user_full_info(chat_id, msg_id, tid)
    elif action in ("cong", "tru"):
        ask_amount_for_user(chat_id, msg_id, tid, kind=action)


def ask_amount_for_user(chat_id, msg_id, tid, kind):
    """Hỏi số tiền cộng/trừ cho 1 user cụ thể."""
    title = "➕ CỘNG TIỀN" if kind == "cong" else "➖ TRỪ TIỀN"
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 Huỷ", callback_data=f"chk_refresh_{tid}"))
    _safe_edit(
        chat_id, msg_id,
        f"<b>{title}</b> cho user <code>{tid}</code>\n\n"
        "Gõ số tiền (chỉ số):\n"
        "Ví dụ: <code>50000</code>\n\n"
        "Hoặc gõ /huy để huỷ.",
        kb,
    )
    bot.register_next_step_handler_by_chat_id(
        chat_id,
        lambda msg: process_amount_for_user(msg, tid, kind),
    )


def process_amount_for_user(message, tid, kind):
    if not is_admin(message.chat.id):
        return
    if (message.text or "").strip().startswith("/"):
        bot.send_message(message.chat.id, "❎ Đã huỷ.")
        show_user_full_info(message.chat.id, None, tid)
        return
    try:
        raw = (message.text or "").strip().replace(",", "").replace(".", "")
        amt = int(raw)
        if amt <= 0:
            raise ValueError("Số tiền phải > 0")
        get_or_create_user(tid)
        if kind == "cong":
            add_balance(tid, amt)
            new_bal = get_balance(tid)
            bot.send_message(
                message.chat.id,
                f"✅ Đã <b>cộng {amt:,}đ</b> cho <code>{tid}</code> {get_user_label(tid)}.\n"
                f"Số dư mới: <b>{new_bal:,}đ</b>",
            )
            try:
                bot.send_message(
                    tid,
                    f"💰 Admin đã cộng cho bạn <b>{amt:,}đ</b>.\n"
                    f"Số dư hiện tại: <b>{new_bal:,}đ</b>",
                )
            except Exception:
                pass
        else:  # tru
            add_balance(tid, -amt)
            new_bal = get_balance(tid)
            bot.send_message(
                message.chat.id,
                f"✅ Đã <b>trừ {amt:,}đ</b> của <code>{tid}</code> {get_user_label(tid)}.\n"
                f"Số dư mới: <b>{new_bal:,}đ</b>",
            )
        # Hiện lại panel user sau khi thao tác
        show_user_full_info(message.chat.id, None, tid)
    except Exception as e:
        bot.send_message(
            message.chat.id,
            f"❌ Sai định dạng ({e}).\nVí dụ đúng: <code>50000</code>",
        )
        show_user_full_info(message.chat.id, None, tid)


# ----- BROADCAST (đa media + 50 luồng) -----
# {admin_id: {"src_chat": int, "src_msg": int, "type": str}}
BROADCAST_STATE = {}

# Cấu hình broadcast
BROADCAST_WORKERS    = 50    # Số luồng song song
BROADCAST_DELAY      = 0.05  # Delay nhẹ (giây) trước mỗi lần gửi để tránh Telegram flood
BROADCAST_RETRY_429  = True  # Tự động retry khi gặp HTTP 429 (Too Many Requests)

# Map content_type → label hiển thị cho admin
CONTENT_TYPE_LABEL = {
    "text":       "📝 Text",
    "photo":      "🖼 Ảnh",
    "video":      "🎬 Video",
    "document":   "📄 File",
    "sticker":    "🎴 Nhãn dán",
    "voice":      "🎙 Voice",
    "audio":      "🎵 Audio",
    "animation":  "🎞 GIF / Animation",
    "video_note": "📹 Video tròn",
    "location":   "📍 Vị trí",
    "contact":    "📇 Contact",
}


def ask_broadcast_text(chat_id, msg_id):
    """Bắt đầu flow Broadcast — bot chờ admin gửi 1 nội dung BẤT KỲ.

    Hỗ trợ: text (HTML), ảnh, video, file, sticker, voice, audio, GIF…
    Bot copy-paste y nguyên (caption, định dạng…) sang TẤT CẢ user.
    """
    _safe_edit(
        chat_id, msg_id,
        "📢 <b>BROADCAST</b>\n\n"
        "Gửi tới đây <b>1 tin nhắn</b> với nội dung muốn broadcast:\n"
        "• 📝 Text (hỗ trợ HTML <code>&lt;b&gt;đậm&lt;/b&gt;</code>, "
        "<code>&lt;i&gt;nghiêng&lt;/i&gt;</code>, link…)\n"
        "• 🖼 Ảnh / 🎬 Video / 📄 File / 🎴 Nhãn dán / 🎙 Voice / 🎵 Audio / 🎞 GIF\n\n"
        "Bot sẽ <b>copy y nguyên</b> tin nhắn của bạn (kèm caption/định dạng) "
        "tới tất cả user <i>không bị ban</i>, sau khi bạn xác nhận.\n\n"
        "Hoặc gõ /huy để huỷ.",
        back_to_admin_markup(),
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_broadcast_input)


def process_broadcast_input(message):
    """Nhận tin nhắn admin gửi (bất kỳ loại nội dung) và hiện preview xác nhận."""
    if not is_admin(message.chat.id):
        return
    # Cancel: text bắt đầu bằng /
    if message.content_type == "text" and (message.text or "").strip().startswith("/"):
        bot.send_message(message.chat.id, "❎ Đã huỷ.", reply_markup=admin_menu_markup())
        return

    # Lưu lại nguồn để dùng copy_message khi gửi
    BROADCAST_STATE[message.chat.id] = {
        "src_chat": message.chat.id,
        "src_msg":  message.message_id,
        "type":     message.content_type,
    }

    # Đếm user (loại trừ user bị ban) để thông báo cho admin
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE IFNULL(is_banned,0)=0")
        cnt = cur.fetchone()["cnt"]
        c.close()

    type_label = CONTENT_TYPE_LABEL.get(message.content_type, message.content_type)

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(
            f"✅ XÁC NHẬN gửi cho {cnt} user",
            callback_data="adm_broadcast_send",
        ),
        InlineKeyboardButton("❌ Huỷ", callback_data="adm_main"),
    )

    bot.send_message(
        message.chat.id,
        "📢 <b>XEM LẠI TIN BROADCAST</b>\n\n"
        f"📦 Loại nội dung: <b>{type_label}</b>\n"
        f"👥 Sẽ gửi cho: <b>{cnt:,} user</b> "
        f"(đã loại trừ user bị ban)\n"
        f"🚀 Tốc độ: <b>{BROADCAST_WORKERS} luồng</b> song song "
        f"(delay {int(BROADCAST_DELAY * 1000)}ms/tin để tránh Telegram chặn)\n\n"
        "👆 Tin gốc bạn vừa gửi ở phía trên là nội dung sẽ được broadcast.\n\n"
        "⚠️ <b>BẠN ĐÃ CHẮC CHẮN MUỐN GỬI CHƯA?</b>",
        reply_markup=kb,
    )


def do_broadcast_send(call):
    state = BROADCAST_STATE.pop(call.from_user.id, None)
    if not state:
        try:
            bot.answer_callback_query(call.id, "❌ Hết hạn, hãy gõ lại")
        except Exception:
            pass
        return
    try:
        bot.edit_message_text(
            "🚀 Đang gửi broadcast... vui lòng đợi.\n"
            f"Tốc độ: {BROADCAST_WORKERS} luồng song song.",
            call.message.chat.id, call.message.message_id,
            parse_mode="HTML",
        )
    except Exception:
        pass
    threading.Thread(
        target=_broadcast_parallel,
        args=(call.message.chat.id, state),
        daemon=True,
    ).start()


def _send_one_broadcast(tid, src_chat, src_msg):
    """Gửi 1 tin (copy_message) cho 1 user, có retry khi gặp 429.

    Trả về True nếu thành công, False nếu thất bại sau khi retry.
    """
    # Delay nhẹ trước mỗi tin để Telegram bot không bị spam-block
    time.sleep(BROADCAST_DELAY)
    try:
        bot.copy_message(tid, src_chat, src_msg)
        return True
    except telebot.apihelper.ApiTelegramException as e:
        # 429 Too Many Requests → ngủ retry_after giây rồi thử lại 1 lần
        if BROADCAST_RETRY_429 and e.error_code == 429:
            retry_after = 1
            try:
                # ApiException của telebot có result_json kèm parameters.retry_after
                retry_after = int(
                    (e.result_json or {}).get("parameters", {}).get("retry_after", 1)
                )
            except Exception:
                pass
            time.sleep(min(retry_after + 0.5, 30))
            try:
                bot.copy_message(tid, src_chat, src_msg)
                return True
            except Exception:
                return False
        # 403 (user blocked bot), 400 (chat not found)…  bỏ qua
        return False
    except Exception:
        return False


def _broadcast_parallel(admin_chat_id, state):
    """Worker chính: dùng ThreadPoolExecutor 50 luồng để gửi song song."""
    src_chat = state["src_chat"]
    src_msg  = state["src_msg"]

    # Lấy danh sách user đích — loại trừ user bị ban
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute(
            "SELECT telegram_id FROM users "
            "WHERE IFNULL(is_banned,0)=0 ORDER BY telegram_id"
        )
        ids = [r["telegram_id"] for r in cur.fetchall()]
        c.close()

    total = len(ids)
    ok = 0
    fail = 0
    counter_lock = threading.Lock()
    progress_msg_id = None

    # Tin nhắn tiến độ — sẽ được edit liên tục
    try:
        progress_msg = bot.send_message(
            admin_chat_id,
            f"📤 <b>Đang gửi…</b>\n\nĐã gửi: <b>0 / {total:,}</b>",
        )
        progress_msg_id = progress_msg.message_id
    except Exception:
        progress_msg_id = None

    def work(tid):
        nonlocal ok, fail
        success = _send_one_broadcast(tid, src_chat, src_msg)
        with counter_lock:
            if success:
                ok += 1
            else:
                fail += 1

    start = time.time()
    with ThreadPoolExecutor(max_workers=BROADCAST_WORKERS) as exe:
        futures = [exe.submit(work, tid) for tid in ids]

        # Cập nhật tiến độ mỗi 2 giây
        last_update = 0
        while True:
            done = sum(1 for f in futures if f.done())
            if done >= total:
                break
            now = time.time()
            if progress_msg_id and now - last_update >= 2:
                try:
                    bot.edit_message_text(
                        f"📤 <b>Đang gửi…</b>\n\n"
                        f"Đã xử lý: <b>{done:,} / {total:,}</b>\n"
                        f"✅ OK: <b>{ok:,}</b> | ❌ Fail: <b>{fail:,}</b>",
                        admin_chat_id, progress_msg_id,
                        parse_mode="HTML",
                    )
                    last_update = now
                except Exception:
                    pass
            time.sleep(0.5)

    elapsed = time.time() - start
    rate = (total / elapsed) if elapsed > 0 else 0

    # Xoá tin tiến độ, gửi tin tổng kết
    if progress_msg_id:
        try:
            bot.delete_message(admin_chat_id, progress_msg_id)
        except Exception:
            pass

    bot.send_message(
        admin_chat_id,
        "✅ <b>BROADCAST HOÀN TẤT</b>\n\n"
        f"👥 Tổng user: <b>{total:,}</b>\n"
        f"📨 Thành công: <b>{ok:,}</b>\n"
        f"❌ Thất bại: <b>{fail:,}</b>\n"
        f"⏱ Thời gian: <b>{elapsed:.1f}s</b> "
        f"(~{rate:.1f} tin/giây)",
        reply_markup=admin_menu_markup(),
    )


# ----- SLASH COMMANDS (giữ làm backup cho power-user) -----
@bot.message_handler(commands=["cong"])
def cmd_cong(m):
    if not is_admin(m.chat.id):
        return
    try:
        _, tid_s, amt_s = m.text.split()
        tid = int(tid_s); amt = int(amt_s)
        get_or_create_user(tid)
        add_balance(tid, amt)
        new_bal = get_balance(tid)
        bot.send_message(
            m.chat.id,
            f"✅ Đã cộng <b>{amt:,}đ</b> cho <code>{tid}</code> {get_user_label(tid)}.\n"
            f"Số dư mới: <b>{new_bal:,}đ</b>",
        )
        try:
            bot.send_message(
                tid,
                f"💰 Admin đã cộng cho bạn <b>{amt:,}đ</b>.\n"
                f"Số dư hiện tại: <b>{new_bal:,}đ</b>",
            )
        except Exception:
            pass
    except Exception:
        bot.send_message(
            m.chat.id,
            "❌ Cú pháp sai. Đúng: <code>/cong &lt;id&gt; &lt;số_tiền&gt;</code>",
        )


@bot.message_handler(commands=["tru"])
def cmd_tru(m):
    if not is_admin(m.chat.id):
        return
    try:
        _, tid_s, amt_s = m.text.split()
        tid = int(tid_s); amt = int(amt_s)
        add_balance(tid, -amt)
        bot.send_message(
            m.chat.id,
            f"✅ Đã trừ <b>{amt:,}đ</b> của <code>{tid}</code> {get_user_label(tid)}.\n"
            f"Số dư hiện tại: <b>{get_balance(tid):,}đ</b>",
        )
    except Exception:
        bot.send_message(
            m.chat.id,
            "❌ Cú pháp sai. Đúng: <code>/tru &lt;id&gt; &lt;số_tiền&gt;</code>",
        )


@bot.message_handler(commands=["sodu"])
def cmd_sodu(m):
    if not is_admin(m.chat.id):
        return
    try:
        _, tid_s = m.text.split()
        tid = int(tid_s)
        bot.send_message(
            m.chat.id,
            f"💰 Số dư <code>{tid}</code> {get_user_label(tid)}: <b>{get_balance(tid):,}đ</b>",
        )
    except Exception:
        bot.send_message(m.chat.id, "❌ Cú pháp: <code>/sodu &lt;id&gt;</code>")


@bot.message_handler(commands=["recent"])
def cmd_recent(m):
    if not is_admin(m.chat.id):
        return
    with db_lock:
        c = db_conn(); cur = c.cursor()
        cur.execute("SELECT * FROM deposits ORDER BY id DESC LIMIT 20")
        rows = [dict(r) for r in cur.fetchall()]
        c.close()
    if not rows:
        bot.send_message(m.chat.id, "📭 Chưa có giao dịch nào.")
        return
    lines = ["💰 <b>20 GIAO DỊCH GẦN NHẤT</b>\n"]
    for r in rows:
        flag = "✅" if r["status"] == "success" else "❌"
        ct = (r["content"] or "")[:40]
        lines.append(
            f"{flag} <code>{r['telegram_id']}</code> | "
            f"<b>{r['amount']:,}đ</b> | <i>{ct}</i>"
        )
    bot.send_message(m.chat.id, "\n".join(lines))


# ================== SEPAY POLLING ==================
def fetch_sepay_transactions(limit=SEPAY_FETCH_LIMIT):
    """Lấy danh sách giao dịch mới nhất từ SePay."""
    try:
        url = "https://my.sepay.vn/userapi/transactions/list"
        headers = {"Authorization": f"Bearer {SEPAY_TOKEN}"}
        r = requests.get(url, headers=headers, params={"limit": limit}, timeout=15)
        data = r.json()
        if isinstance(data, dict):
            return data.get("transactions", []) or []
    except Exception as e:
        print(f"[SePay] Lỗi fetch: {e}")
    return []


def parse_telegram_id_from_content(content: str):
    """Tách Telegram ID từ nội dung CK.

    Logic:
      • Bỏ qua nếu nội dung có pattern "NAP <id>" (đó là cú pháp của BOT cũ).
      • Tìm tất cả chuỗi 6–12 chữ số → lấy chuỗi DÀI NHẤT làm Telegram ID.
      • ID phải > 100000 (Telegram ID hợp lệ thường 8–10 chữ số).
    """
    if not content:
        return None
    upper = content.upper()
    if re.search(r"\bNAP\s+\d+", upper):
        return None  # bot THUANBOT cũ xử lý — bỏ qua để tránh tranh chấp
    candidates = re.findall(r"\d{6,12}", content)
    if not candidates:
        return None
    best = max(candidates, key=len)
    try:
        tid = int(best)
        if tid > 100000:
            return tid
    except Exception:
        pass
    return None


def handle_new_transaction(tx):
    """Xử lý 1 giao dịch SePay: parse, kiểm tra hạn mức, cộng tiền hoặc từ chối."""
    sepay_id = str(tx.get("id", "") or "")
    if not sepay_id or is_deposit_processed(sepay_id):
        return

    try:
        amount = int(float(tx.get("amount_in", 0) or 0))
    except Exception:
        amount = 0
    if amount <= 0:
        return  # giao dịch ra hoặc 0đ — bỏ qua

    content = (tx.get("transaction_content", "") or "").strip()
    tid = parse_telegram_id_from_content(content)
    if tid is None:
        return  # không tách được Telegram ID → bỏ qua, không log

    # User đã chuyển khoản xong → xoá QR cũ trong chat của họ
    _delete_old_qr(tid)

    # ----- DƯỚI HẠN MỨC: từ chối, không hoàn -----
    if amount < MIN_DEPOSIT:
        log_deposit(sepay_id, tid, amount, content, status="rejected_low_amount")
        try:
            bot.send_message(
                tid,
                "❌ <b>NẠP SAI HẠN MỨC</b>\n\n"
                f"Bạn vừa chuyển <b>{amount:,}đ</b> với nội dung:\n"
                f"<code>{content}</code>\n\n"
                f"⚠️ Số tiền tối thiểu: <b>{MIN_DEPOSIT:,}đ</b>.\n"
                "❗️ Theo quy định, số tiền dưới mức tối thiểu sẽ "
                "<b>KHÔNG được cộng vào tài khoản</b> và "
                "<b>KHÔNG hoàn lại</b>.\n\n"
                "Vui lòng đọc kỹ hướng dẫn trước khi nạp lần sau!",
            )
        except Exception:
            pass
        try:
            bot.send_message(
                ADMIN_ID,
                "⚠️ <b>NẠP DƯỚI HẠN MỨC</b>\n"
                f"User: <code>{tid}</code> {get_user_label(tid)}\n"
                f"Số tiền: <b>{amount:,}đ</b>\n"
                f"Nội dung: <i>{content}</i>",
            )
        except Exception:
            pass
        return

    # ----- HỢP LỆ: cộng tiền -----
    get_or_create_user(tid)
    add_balance(tid, amount)
    log_deposit(sepay_id, tid, amount, content, status="success")
    new_bal = get_balance(tid)

    try:
        bot.send_message(
            tid,
            "✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
            f"💰 Số tiền: <b>+{amount:,}đ</b>\n"
            f"💳 Số dư mới: <b>{new_bal:,}đ</b>\n"
            f"📝 Nội dung: <i>{content}</i>\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}\n\n"
            "Cảm ơn bạn! 🎉",
        )
    except Exception:
        pass

    try:
        bot.send_message(
            ADMIN_ID,
            "💰 <b>NẠP THÀNH CÔNG</b>\n"
            f"User: <code>{tid}</code> {get_user_label(tid)}\n"
            f"Số tiền: <b>{amount:,}đ</b>\n"
            f"Số dư mới: <b>{new_bal:,}đ</b>\n"
            f"Nội dung: <i>{content}</i>",
        )
    except Exception:
        pass


def sepay_polling_loop():
    print(f"[SePay] Polling loop bắt đầu — interval {SEPAY_POLL_INTERVAL}s")
    while True:
        try:
            txs = fetch_sepay_transactions(limit=SEPAY_FETCH_LIMIT)
            for tx in txs:
                try:
                    handle_new_transaction(tx)
                except Exception as e:
                    print(f"[SePay] Lỗi xử lý 1 giao dịch: {e}")
        except Exception as e:
            print(f"[SePay] Lỗi vòng lặp: {e}")
        time.sleep(SEPAY_POLL_INTERVAL)


# ================== MAIN ==================
def main():
    init_db()
    print(f"[NEWBOT] Khởi động… Admin: {ADMIN_ID}")
    print(f"[NEWBOT] Bank: {BANK_NAME} - {BANK_ACC} - {BANK_HOLDER}")
    print(f"[NEWBOT] MIN deposit: {MIN_DEPOSIT:,}đ — Poll {SEPAY_POLL_INTERVAL}s")

    # Đăng ký module Reg (Tạo Nick) — gói trong try/except để bot vẫn chạy được
    # nếu reg/ chưa cài đặt được hoặc REG_API_SERVER chưa có deps.
    try:
        from reg.handlers import register as register_reg_handlers
        register_reg_handlers(
            bot, ADMIN_ID, _user_allowed,
            get_balance_fn=get_balance,
            reserve_balance_fn=reserve_balance,
            add_balance_fn=add_balance,
            log_expense_fn=log_expense,
            get_price_fn=get_price,
        )
        print("[NEWBOT] ✅ Đã load module Reg (Tạo Nick): OKVIP + KJC")
    except Exception as e:
        print(f"[NEWBOT] ⚠️ Không load được module Reg: {e}")
        print(f"[NEWBOT]    Bot vẫn chạy được — chỉ thiếu nút 🚀 Tạo Nick.")
        print(f"[NEWBOT]    Hãy cài: pip install curl_cffi rsa pycryptodome")

    # Thread polling SePay
    t = threading.Thread(target=sepay_polling_loop, daemon=True)
    t.start()

    # Bot polling — auto restart nếu crash
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10)
        except Exception as e:
            print(f"[Bot] Crashed: {e} — restart sau 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
