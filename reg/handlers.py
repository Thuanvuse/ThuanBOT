
# -*- coding: utf-8 -*-
"""
NEWBOT — Reg Handlers (Telegram)
==================================
Telegram handlers cho menu "🚀 Tạo Nick".

LUỒNG (multi-site + multi-line):
  1. User bấm "🚀 Tạo Nick" trên reply menu chính
  2. Bot hiện submenu: [🏠 REG NHÀ OKVIP] [🏠 REG NHÀ KJC]
  3. Chọn nhà → hiện danh sách site với checkbox ✅/❌
       • Toggle từng site bằng cách bấm
       • Có nút "✅ Chọn tất cả" / "❌ Bỏ chọn tất cả"
       • Nút "➡️ Tiếp tục (N site)" hiện khi đã chọn ≥1
  4. (OKVIP) Sau khi tiếp tục → chọn mode chung:
       ⚡ CHỈ TẠO  hoặc  🎁 TẠO + XÁC THỰC + KM
     (KJC) Vào ngay input (chỉ có 1 mode = simple)
  5. Bot hỏi nhập NHIỀU DÒNG, mỗi dòng là 1 bộ:
       proxy|TÊN|STK|BANK
  6. Tổng số nick = (số dòng input) × (số site đã chọn)
  7. Bot tạo job pool, chạy song song tối đa 50 luồng (REG_MAX_WORKERS),
     các job sau vào hàng chờ.
  8. Live progress (edit message mỗi 2s), summary cuối kèm chi tiết từng acc.

Cách import vào bot.py:
    from reg.handlers import register as register_reg_handlers
    register_reg_handlers(bot, ADMIN_ID, _user_allowed)
"""
import os
import sys
import json
import time
import subprocess
import threading
import re
import html
import io
from concurrent.futures import ThreadPoolExecutor

import requests

from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ─── CẤU HÌNH ─────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER_PATH = os.path.join(HERE, "runner.py")
NEWBOT_DIR = os.path.dirname(HERE)
THUANBOT_DIR = os.path.dirname(NEWBOT_DIR)
REG_API_SERVER_DIR = os.path.join(THUANBOT_DIR, "REG_API_SERVER")

OKVIP_SITES = ["cm88", "sc88", "f168", "c168", "fly88"]
KJC_SITES   = ["llwin", "gg88"]

REG_MAX_WORKERS    = 50    # Số luồng song song tối đa
REG_MAX_LINES      = 100   # Số dòng input tối đa (sanity)
REG_MAX_JOBS       = 250   # Số job tổng tối đa (lines × sites)
REG_TIMEOUT_PER_JOB = 240  # Timeout 1 job (giây)
PROGRESS_UPDATE_INTERVAL = 2.5  # Cập nhật progress mỗi N giây
TG_MAX_MESSAGE = 3800      # Giới hạn message Telegram (4096), chừa buffer

PIN_DEFAULT = "111222"     # Mã PIN mặc định
PROXY_CHECK_TIMEOUT = 10   # Timeout check 1 proxy (giây)
PROXY_CHECK_WORKERS = 20   # Số luồng check proxy song song
PROXY_TEST_URL = "https://geo.myip.link/"  # URL kiểm tra proxy live
KJC_PRICE = 1234          # Giá mặc định 1 acc KJC; giá thật lấy từ DB nếu có
OKVIP_SIMPLE_PRICE = 1234
OKVIP_FULL_PRICE = 4999
OKVIP_PROMO_PRICE = 39

_REG_ADMIN_ID = None
_GET_BALANCE_FN = None
_RESERVE_BALANCE_FN = None
_ADD_BALANCE_FN = None
_LOG_EXPENSE_FN = None
_GET_PRICE_FN = None

_PROCESS_LOCK = threading.Lock()
_PROCESS_SEQ = 0
_ACTIVE_PROCESSES = {}
_LAST_OKVIP_KM_LOCK = threading.Lock()
_LAST_OKVIP_KM_RESULTS = {}

# ─── DANH SÁCH BANK CHUẨN (copy từ BOT THUANBOT cũ) ──────
# Mỗi bank có:
#   • code = mã user gõ vào (vd "MBBANK", "VIETCOMB")
#   • name = tên đầy đủ hiển thị (vd "MB BANK", "Vietcom Bank")
BANK_DATA = [
    {"name": "Vikki Bank",            "code": "DONGABANK"},
    {"name": "LIO Bank",              "code": "LIOBANK"},
    {"name": "STANCHART (SCVN)",      "code": "SCB"},
    {"name": "BACABANK (BAB)",        "code": "BAB"},
    {"name": "SeABank",               "code": "SEABANK"},
    {"name": "Nam A BANK",            "code": "NABANK"},
    {"name": "Vietcom Bank",          "code": "VIETCOMB"},
    {"name": "NCB",                   "code": "NCB"},
    {"name": "VPBANK",                "code": "VPBANK"},
    {"name": "ACB",                   "code": "ACB"},
    {"name": "VIB",                   "code": "VIB"},
    {"name": "BIDV",                  "code": "BIDV"},
    {"name": "SACOMBANK (STB)",       "code": "SBANK"},
    {"name": "TPBank",                "code": "TPBANK"},
    {"name": "TECHCOMBANK (TCB)",     "code": "TECHCOMB"},
    {"name": "Vietin Bank (VTB)",     "code": "VIETINBA"},
    {"name": "HDBank",                "code": "HDBANK"},
    {"name": "AGRIBANK",              "code": "AGRIBANK"},
    {"name": "OCB",                   "code": "OCBANK"},
    {"name": "MB BANK",               "code": "MBBANK"},
    {"name": "EXIMBANK (EIB)",        "code": "EXIM"},
    {"name": "SHB",                   "code": "SHB"},
    {"name": "LIENVIETPOSTBANK (LPB)","code": "LVPBANK"},
    {"name": "PVcom Bank",            "code": "PVBANK"},
    {"name": "MARITIMEBANK (MSB)",    "code": "MARIBANK"},
    {"name": "ABBANK (ABB)",          "code": "ABB"},
    {"name": "KIENLONGBANK (KLB)",    "code": "KLBANK"},
    {"name": "BAOVIETBANK (BVB)",     "code": "BVB"},
    {"name": "SCB BANK (SCB)",        "code": "SCBBANK"},
    {"name": "CAKE",                  "code": "CAKE"},
    {"name": "SHINHAN BANK",          "code": "SHANBANK"},
    {"name": "PG BANK",               "code": "PGBA"},
    {"name": "COOPBANK",              "code": "COOPBANK"},
    {"name": "DONGABANK (DAB/EAB)",   "code": "DAB"},
    {"name": "BVBank",                "code": "BVBBANK"},
    {"name": "SAIGONBANK (SGB)",      "code": "SABANK"},
    {"name": "GPBANK",                "code": "GPBANK"},
    {"name": "VIET BANK",             "code": "VIETBANK"},
]
VALID_BANKS = {b["code"].upper() for b in BANK_DATA}
KJC_BANK_CODE_MAP = {
    "BAB": "BACABANK",
    "SEABANK": "SEABANK",
    "NABANK": "NAMABANK",
    "VIETCOMB": "VIETCOMBANK",
    "VPBANK": "VPBANK",
    "ACB": "ACBANK",
    "VIB": "VIBBANK",
    "BIDV": "BIDV",
    "SBANK": "SACOMBANK",
    "TPBANK": "TPBANK",
    "TECHCOMB": "TECHCOMBANK",
    "VIETINBA": "VIETINBANK",
    "HDBANK": "HDBANK",
    "AGRIBANK": "AGRIBANK",
    "MBBANK": "MBBANK",
    "EXIM": "EXIMBANK",
    "SHB": "SHBANK",
    "LVPBANK": "LIENVIETPOSTBANK",
    "PVBANK": "PVCOMBANK",
    "MARIBANK": "MARITIMEBANK",
    "ABB": "ABBANK",
    "BVB": "BAOVIETBANK",
    "PGBA": "PGBANK",
}


def _banks_help_text(house=None):
    """Tạo text hướng dẫn list mã bank chuẩn (HTML format)."""
    if house == "kjc":
        lines = [
            "📋 <b>MÃ BANK KJC HỖ TRỢ</b>",
            "<i>Nhập mã theo list bot cũ; bot tự đổi sang mã KJC khi gửi API.</i>",
            "",
        ]
        for b in BANK_DATA:
            code = b["code"].upper()
            api_code = KJC_BANK_CODE_MAP.get(code)
            if api_code:
                suffix = f" → {api_code}" if api_code != code else ""
                lines.append(f"• <code>{code}</code>{suffix} — {b['name']}")
        return "\n".join(lines)

    lines = ["📋 <b>DANH SÁCH MÃ BANK CHUẨN</b>", ""]
    for b in BANK_DATA:
        lines.append(f"• <code>{b['code']}</code> — {b['name']}")
    return "\n".join(lines)


def _html_escape(text):
    return html.escape(str(text), quote=False)


def _mode_label(house, mode):
    if house == "kjc":
        return "🧩 Tạo + Bank + PIN + DOB"
    return "⚡ Chỉ tạo" if mode == "simple" else "🎁 Tạo + Xác thực + KM"


def _input_syntax_line(house):
    if house == "kjc":
        return "<code>proxy|TÊN|STK|BANK|SĐT</code>"
    return "<code>proxy|TÊN|STK|BANK</code>"


def _short_error_message(msg):
    raw = str(msg or "").strip()
    low = raw.lower()
    if "status_code" in low and "404" in low and "41001" in low:
        return "API chỉ tạo cũ lỗi"
    if (
        "phone_dup" in low
        or "_phone_dup" in low
        or "số điện thoại di động đã được liên kết" in low
        or "sdt" in low and "đã" in low and "đăng" in low
        or "phone" in low and ("dup" in low or "linked" in low or "exist" in low)
    ):
        return "SĐT đã đăng ký"
    if (
        "tài khoản rút" in low
        or "tai khoan rut" in low
        or ("bank" in low and ("trùng" in low or "duplicate" in low or "exist" in low))
        or ("ngân hàng" in low and ("đã" in low or "trùng" in low))
    ):
        return "Trùng Bank"
    if "timeout" in low:
        return "Timeout"
    if "proxy" in low:
        return "Proxy lỗi"
    return raw[:60]


def _is_okvip_refundable_error(raw_msg, short_msg=""):
    raw = str(raw_msg or "")
    low = raw.lower()
    short = str(short_msg or "")
    if short in ("Trùng Bank", "SĐT đã đăng ký"):
        return False
    if (
        "tài khoản rút" in low
        or "tai khoan rut" in low
        or ("bank" in low and ("exist" in low or "duplicate" in low or "trùng" in low))
        or ("ngân hàng" in low and ("đã" in low or "trùng" in low))
    ):
        return False
    if any(k in low for k in [
        "_sms_timeout",
        "sms_timeout",
        "không lấy được mã otp",
        "khong lay duoc ma otp",
        "không nhận được otp",
        "khong nhan duoc otp",
        "vượt quá số lần thử sms",
        "vuot qua so lan thu sms",
    ]):
        return True
    if ("otp" in low or "sms" in low or "sms_verify" in low or "verify phone" in low) and any(k in low for k in [
        "timeout", "quá giờ", "qua gio", "failed", "fail", "thất bại", "that bai", "không", "khong",
    ]):
        return True
    if ("xác thực" in low or "xac thuc" in low or "xác minh" in low or "xac minh" in low) and any(k in low for k in [
        "sđt", "sdt", "số điện thoại", "so dien thoai", "phone", "sms", "otp",
    ]) and any(k in low for k in [
        "không", "khong", "lỗi", "loi", "fail", "thất bại", "that bai", "quá giờ", "qua gio", "timeout",
    ]):
        return True
    return False


def _syntax_guidance(house):
    syntax_line = _input_syntax_line(house)
    return (
        f"Gõ lại cú pháp {syntax_line} (mỗi dòng 1 bộ) hoặc /huy để huỷ.\n"
        "⚠️ Các phần phải tách nhau bằng dấu <code>|</code>.\n"
        "⚠️ Mã BANK phải viết liền, <b>không dấu cách</b> "
        "(vd <code>MBBANK</code>, không nhập <code>MB BANK</code>)."
    )


def _fmt_money(amount):
    return f"{int(amount):,}đ"


def _get_kjc_price():
    return _get_price_value("price_reg_kjc", KJC_PRICE)


def _get_price_value(key, default):
    if _GET_PRICE_FN:
        try:
            return int(_GET_PRICE_FN(key))
        except Exception:
            pass
    return int(default)


def _get_okvip_mode_price(mode):
    if mode == "full":
        return _get_price_value("price_reg_okvip_full", OKVIP_FULL_PRICE)
    return _get_price_value("price_reg_okvip_simple", OKVIP_SIMPLE_PRICE)


def _get_okvip_promo_price():
    return _get_price_value("price_okvip_promo", OKVIP_PROMO_PRICE)


def _get_reg_unit_price(house, mode):
    if house == "kjc":
        return _get_kjc_price()
    if house == "okvip":
        return _get_okvip_mode_price(mode)
    return 0


def _billing_title(house, mode):
    if house == "okvip" and mode == "simple":
        return "OKVIP CHỈ TẠO"
    if house == "okvip" and mode == "full":
        return "OKVIP XÁC THỰC + KM"
    return str(house or "").upper()


def _km_text_pending(label="", msg=""):
    text = f"{label} {msg}".upper()
    return "ĐANG CHỜ" in text or "DANG CHO" in text or "PENDING" in text


def _km_text_received(label="", msg=""):
    text = f"{label} {msg}".upper()
    return "ĐÃ NHẬN" in text or "DA NHAN" in text or "SUCCESS" in text


def _save_last_okvip_km_results(chat_id, records):
    with _LAST_OKVIP_KM_LOCK:
        _LAST_OKVIP_KM_RESULTS[str(chat_id)] = [dict(r) for r in records]


def _get_last_okvip_km_results(chat_id):
    with _LAST_OKVIP_KM_LOCK:
        return [dict(r) for r in _LAST_OKVIP_KM_RESULTS.get(str(chat_id), [])]


def _okvip_km_status_text(rec):
    label = str(rec.get("km_label") or "").strip()
    msg = str(rec.get("km_msg") or "").strip()
    emoji = str(rec.get("km_emoji") or "").strip()
    status = f"{emoji} {label}".strip() if emoji else label
    return f"{status}: {msg}" if msg else status


def _send_okvip_km_export(bot, call):
    chat_id = call.message.chat.id
    records = _get_last_okvip_km_results(chat_id)
    if not records:
        try:
            bot.answer_callback_query(call.id, "❌ Không có dữ liệu để xuất file", show_alert=True)
        except Exception:
            pass
        return

    lines = []
    for r in records:
        lines.append(
            f"{r.get('username','')}|{r.get('password','')}|"
            f"{r.get('realname','')}|{r.get('pin') or PIN_DEFAULT}|"
            f"{r.get('proxy','')}|[{str(r.get('site','')).upper()}]|"
            f"{_okvip_km_status_text(r)}"
        )
    file_io = io.BytesIO("\n".join(lines).encode("utf-8"))
    file_io.name = f"OKVIP_KM_{chat_id}_{int(time.time())}.txt"
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    bot.send_document(
        chat_id,
        file_io,
        caption="📥 File kết quả OKVIP KM (User|Pass|Tên|PIN|Proxy|[Site]|KM)",
    )


def _check_okvip_km_once(site, username, proxy):
    try:
        if REG_API_SERVER_DIR not in sys.path:
            sys.path.insert(0, REG_API_SERVER_DIR)
        from promo_km import _do_km_check, KM_SITES
        site = (site or "").lower()
        if site not in KM_SITES:
            return "TỪ CHỐI", f"Site {site.upper()} không hỗ trợ KM", "❌"
        return _do_km_check(
            user=username,
            proxy_str=proxy or "",
            config=KM_SITES[site],
        )
    except Exception as e:
        return "TỪ CHỐI", str(e)[:200], "❌"


def _retry_last_okvip_pending_km(bot, call):
    chat_id = call.message.chat.id
    records = _get_last_okvip_km_results(chat_id)
    pending = [r for r in records if _km_text_pending(r.get("km_label"), r.get("km_msg"))]
    if not records:
        try:
            bot.answer_callback_query(call.id, "❌ Không tìm thấy batch OKVIP gần nhất", show_alert=True)
        except Exception:
            pass
        return
    if not pending:
        try:
            bot.answer_callback_query(call.id, "✅ Không còn acc nào Đang chờ KM", show_alert=True)
        except Exception:
            pass
        return

    promo_price = _get_okvip_promo_price()
    total_cost = len(pending) * promo_price
    if promo_price > 0:
        if not (_GET_BALANCE_FN and _RESERVE_BALANCE_FN):
            try:
                bot.answer_callback_query(call.id, "❌ Chưa cấu hình thanh toán KM", show_alert=True)
            except Exception:
                pass
            return
        balance = int(_GET_BALANCE_FN(chat_id) or 0)
        if balance < total_cost:
            try:
                bot.answer_callback_query(
                    call.id,
                    f"❌ Không đủ tiền! Cần {_fmt_money(total_cost)} để check {len(pending)} nick",
                    show_alert=True,
                )
            except Exception:
                pass
            return
        ok_charge, new_balance = _RESERVE_BALANCE_FN(chat_id, total_cost)
        if not ok_charge:
            try:
                bot.answer_callback_query(call.id, "❌ Không đủ tiền!", show_alert=True)
            except Exception:
                pass
            return
    else:
        new_balance = int(_GET_BALANCE_FN(chat_id) or 0) if _GET_BALANCE_FN else 0

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    msg = bot.send_message(
        chat_id,
        f"⏳ <b>Đang check lại KM cho {len(pending)} acc Đang chờ...</b>\n"
        f"💵 Giá: <b>{_fmt_money(promo_price)}/nick</b>\n"
        f"🔒 Đã trừ: <b>{_fmt_money(total_cost)}</b>\n"
        f"💰 Số dư còn lại: <b>{_fmt_money(new_balance)}</b>",
        parse_mode="HTML",
    )

    def _run():
        updated = []
        counts = {"received": 0, "pending": 0, "denied": 0}
        detail_lines = []
        for rec in records:
            if _km_text_pending(rec.get("km_label"), rec.get("km_msg")):
                label, km_msg, emoji = _check_okvip_km_once(
                    rec.get("site"),
                    rec.get("username"),
                    rec.get("proxy"),
                )
                rec["km_label"] = label or "TỪ CHỐI"
                rec["km_msg"] = km_msg or ""
                rec["km_emoji"] = emoji or ""
                detail_lines.append(
                    f"• <b>{_html_escape(str(rec.get('site','')).upper())}</b> | "
                    f"<code>{_html_escape(rec.get('username',''))}</code> | "
                    f"{_html_escape(rec.get('km_emoji') or '')} "
                    f"<b>{_html_escape(rec.get('km_label',''))}</b>"
                )
            if _km_text_received(rec.get("km_label"), rec.get("km_msg")):
                counts["received"] += 1
            elif _km_text_pending(rec.get("km_label"), rec.get("km_msg")):
                counts["pending"] += 1
            else:
                counts["denied"] += 1
            updated.append(rec)

        _save_last_okvip_km_results(chat_id, updated)
        report = (
            "✅ <b>XONG XIN LẠI / CHECK KM!</b>\n\n"
            f"🎁 Đã nhận: <b>{counts['received']}</b>\n"
            f"⏳ Đang chờ: <b>{counts['pending']}</b>\n"
            f"❌ Từ chối/Lỗi: <b>{counts['denied']}</b>\n\n"
            f"💵 Đã tính phí: <b>{len(pending)}</b> nick × "
            f"<b>{_fmt_money(promo_price)}</b> = <b>{_fmt_money(total_cost)}</b>"
        )
        if total_cost and _LOG_EXPENSE_FN:
            try:
                _LOG_EXPENSE_FN(
                    chat_id, total_cost, "okvip_promo",
                    f"Check lại KM OKVIP {len(pending)} acc",
                )
            except Exception:
                pass
        if detail_lines:
            report += "\n\n📋 <b>ACC VỪA CHECK:</b>\n" + "\n".join(detail_lines[:30])
            if len(detail_lines) > 30:
                report += f"\n... còn {len(detail_lines) - 30} acc"
        markup = InlineKeyboardMarkup()
        if counts["pending"] > 0:
            markup.add(InlineKeyboardButton(
                "🔄 Check tiếp các acc Đang chờ",
                callback_data="reg_retry_okvip_km_last",
            ))
        markup.add(InlineKeyboardButton(
            "📥 Tải danh sách acc",
            callback_data="reg_export_okvip_km_last",
        ))
        try:
            bot.edit_message_text(
                report,
                chat_id,
                msg.message_id,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except Exception:
            bot.send_message(
                chat_id,
                report,
                reply_markup=markup,
                parse_mode="HTML",
            )

    threading.Thread(target=_run, daemon=True).start()


def _label_from_tg_user(user, fallback_id=None):
    if user is None:
        return str(fallback_id or "")
    username = (getattr(user, "username", "") or "").strip()
    first = (getattr(user, "first_name", "") or "").strip()
    uid = getattr(user, "id", None) or fallback_id or ""
    if username:
        return f"@{username}"
    if first:
        return first
    return str(uid)


def _new_process_id():
    global _PROCESS_SEQ
    with _PROCESS_LOCK:
        _PROCESS_SEQ += 1
        seq = _PROCESS_SEQ
    return time.strftime("REG%H%M%S") + f"-{seq}"


def _create_process(meta):
    pid = _new_process_id()
    now = time.time()
    proc = {
        "id": pid,
        "created_at": now,
        "updated_at": now,
        "status": "checking_proxy",
        "state": {
            "total": meta.get("total", 0),
            "done": 0,
            "ok": 0,
            "fail": 0,
            "queued": meta.get("total", 0),
            "running": 0,
        },
        "active_workers": {},
        **meta,
    }
    with _PROCESS_LOCK:
        _ACTIVE_PROCESSES[pid] = proc
    return pid


def _update_process(pid, **kwargs):
    with _PROCESS_LOCK:
        proc = _ACTIVE_PROCESSES.get(pid)
        if not proc:
            return None
        for k, v in kwargs.items():
            proc[k] = v
        proc["updated_at"] = time.time()
        return dict(proc)


def _remove_process(pid):
    with _PROCESS_LOCK:
        return _ACTIVE_PROCESSES.pop(pid, None)


def _worker_start(pid, idx, job):
    worker = {
        "idx": idx + 1,
        "thread_id": threading.get_ident(),
        "site": (job.get("site") or "").upper(),
        "realname": job.get("realname", ""),
        "phone": job.get("phone", ""),
        "started_at": time.time(),
    }
    with _PROCESS_LOCK:
        proc = _ACTIVE_PROCESSES.get(pid)
        if proc:
            proc.setdefault("active_workers", {})[idx] = worker
            proc["updated_at"] = time.time()


def _worker_done(pid, idx):
    with _PROCESS_LOCK:
        proc = _ACTIVE_PROCESSES.get(pid)
        if proc:
            proc.setdefault("active_workers", {}).pop(idx, None)
            proc["updated_at"] = time.time()


def _snapshot_processes():
    with _PROCESS_LOCK:
        snap = []
        for proc in _ACTIVE_PROCESSES.values():
            p = dict(proc)
            p["state"] = dict(proc.get("state") or {})
            p["active_workers"] = {
                k: dict(v) for k, v in (proc.get("active_workers") or {}).items()
            }
            snap.append(p)
        return snap


def _format_process_check():
    procs = sorted(_snapshot_processes(), key=lambda p: p.get("created_at", 0))
    total_threads = sum(len(p.get("active_workers") or {}) for p in procs)
    if not procs:
        return "✅ <b>Không có tiến trình REG nào đang chạy.</b>"

    now = time.time()
    lines = [
        "🧵 <b>CHECK TIẾN TRÌNH REG</b>",
        "",
        f"📌 Tiến trình đang chạy: <b>{len(procs)}</b>",
        f"🏃 Luồng đang xử lý: <b>{total_threads}</b>",
        "━━━━━━━━━━━━━",
    ]
    for p in procs:
        st = p.get("state") or {}
        elapsed = now - p.get("created_at", now)
        sites = ", ".join(s.upper() for s in p.get("sites", []))
        lines.extend([
            f"🆔 <code>{p.get('id')}</code> | <b>{p.get('status')}</b>",
            f"👤 {p.get('user_label')} — <code>{p.get('user_id')}</code>",
            f"🏠 {str(p.get('house','')).upper()} | 🌐 {sites}",
            f"📊 {st.get('done',0)}/{st.get('total',0)} | ✅ {st.get('ok',0)} | "
            f"❌ {st.get('fail',0)} | 🏃 {st.get('running',0)} | ⏳ {st.get('queued',0)}",
            f"⏱ {elapsed:.1f}s",
        ])
        if p.get("chargeable"):
            price = int(p.get("unit_price") or _get_reg_unit_price(p.get("house"), p.get("mode")))
            title = _billing_title(p.get("house"), p.get("mode"))
            lines.append(
                f"💵 {title}: {_fmt_money(price)}/acc | "
                f"dự kiến {_fmt_money(p.get('planned_cost', 0))} | "
                f"đã trừ {_fmt_money(p.get('reserved_cost', 0))}"
            )
        note = p.get("note")
        if note:
            lines.append(f"📝 {note}")
        workers = list((p.get("active_workers") or {}).values())
        if workers:
            lines.append("🔧 <b>Luồng đang chạy:</b>")
            for w in workers[:20]:
                w_elapsed = now - w.get("started_at", now)
                desc = (
                    f"   • T{w.get('idx')} / thread <code>{w.get('thread_id')}</code>: "
                    f"{w.get('site')} | {w.get('realname')}"
                )
                if w.get("phone"):
                    desc += f" | {w.get('phone')}"
                desc += f" | {w_elapsed:.1f}s"
                lines.append(desc)
            if len(workers) > 20:
                lines.append(f"   ... còn {len(workers) - 20} luồng")
        lines.append("━━━━━━━━━━━━━")
    return "\n".join(lines)


def _notify_admin(bot, text):
    if not _REG_ADMIN_ID:
        return
    try:
        bot.send_message(_REG_ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        try:
            bot.send_message(_REG_ADMIN_ID, text)
        except Exception:
            pass


def _notify_admin_process_start(bot, pid):
    proc = _update_process(pid) or {}
    sites = ", ".join(s.upper() for s in proc.get("sites", []))
    text = (
        "🚀 <b>USER TẠO TIẾN TRÌNH REG</b>\n\n"
        f"🆔 Process: <code>{pid}</code>\n"
        f"👤 User: {proc.get('user_label')} — <code>{proc.get('user_id')}</code>\n"
        f"🏠 Nhà: <b>{str(proc.get('house','')).upper()}</b>\n"
        f"🌐 Sites: <b>{sites}</b>\n"
        f"📊 Tổng job: <b>{proc.get('total', 0)}</b>\n"
        f"⚙️ Mode: <b>{_mode_label(proc.get('house'), proc.get('mode'))}</b>"
    )
    if proc.get("chargeable"):
        price = int(proc.get("unit_price") or _get_reg_unit_price(proc.get("house"), proc.get("mode")))
        text += (
            f"\n💵 Giá: <b>{_fmt_money(price)}/acc</b>"
            f"\n📌 Dự kiến cần: <b>{_fmt_money(proc.get('planned_cost', 0))}</b>"
            f"\n🔒 Trừ sau khi proxy OK: <b>{_fmt_money(proc.get('reserved_cost', 0))}</b>"
        )
    _notify_admin(bot, text)


def _notify_admin_process_end(bot, proc, summary_text):
    if not proc:
        return
    st = proc.get("state") or {}
    text = (
        "🏁 <b>TIẾN TRÌNH REG KẾT THÚC</b>\n\n"
        f"🆔 Process: <code>{proc.get('id')}</code>\n"
        f"👤 User: {proc.get('user_label')} — <code>{proc.get('user_id')}</code>\n"
        f"🏠 Nhà: <b>{str(proc.get('house','')).upper()}</b>\n"
        f"📊 Tổng: <b>{st.get('total',0)}</b> | ✅ <b>{st.get('ok',0)}</b> | "
        f"❌ <b>{st.get('fail',0)}</b>\n"
        f"⏱ Thời gian: <b>{proc.get('elapsed', 0):.1f}s</b>"
    )
    if proc.get("chargeable"):
        text += (
            f"\n💰 Thu thực tế: <b>{_fmt_money(proc.get('net_cost', 0))}</b>"
            f"\n↩️ Hoàn lại: <b>{_fmt_money(proc.get('refunded_cost', 0))}</b>"
        )
    _notify_admin(bot, text)
    for chunk in _split_text_for_telegram(summary_text):
        _notify_admin(bot, chunk)
        time.sleep(0.1)


# State per chat: {chat_id: {"house", "sites": set, "mode"}}
_FLOW = {}
_FLOW_LOCK = threading.Lock()


# ─── STATE HELPERS ────────────────────────────────────────
def _set_flow(chat_id, **kwargs):
    with _FLOW_LOCK:
        s = _FLOW.setdefault(chat_id, {"sites": set()})
        s.update(kwargs)


def _get_flow(chat_id):
    with _FLOW_LOCK:
        return dict(_FLOW.get(chat_id, {}))


def _clear_flow(chat_id):
    with _FLOW_LOCK:
        _FLOW.pop(chat_id, None)


def _toggle_site(chat_id, site):
    with _FLOW_LOCK:
        s = _FLOW.setdefault(chat_id, {"sites": set()})
        sites = s.setdefault("sites", set())
        if site in sites:
            sites.remove(site)
        else:
            sites.add(site)


def _toggle_all_sites(chat_id, all_sites):
    with _FLOW_LOCK:
        s = _FLOW.setdefault(chat_id, {"sites": set()})
        sites = s.setdefault("sites", set())
        if sites == set(all_sites):
            sites.clear()
        else:
            sites.update(all_sites)


def _get_selected_sites(chat_id):
    with _FLOW_LOCK:
        return set(_FLOW.get(chat_id, {}).get("sites", set()))


# ─── KEYBOARDS ────────────────────────────────────────────
def _kb_house():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🏠 REG NHÀ OKVIP", callback_data="reg_house_okvip"))
    kb.row(InlineKeyboardButton("🏠 REG NHÀ KJC", callback_data="reg_house_kjc"))
    kb.row(InlineKeyboardButton("❌ Đóng", callback_data="reg_close"))
    return kb


def _kb_sites_multi(house, selected_sites):
    """Inline keyboard cho multi-select sites (✅/❌ + chọn tất cả + tiếp tục)."""
    sites_list = OKVIP_SITES if house == "okvip" else KJC_SITES
    kb = InlineKeyboardMarkup(row_width=2)

    # Mỗi site là 1 nút toggle
    btns = []
    for s in sites_list:
        icon = "✅" if s in selected_sites else "❌"
        btns.append(InlineKeyboardButton(
            f"{icon} {s.upper()}",
            callback_data=f"reg_toggle_{house}_{s}",
        ))
    kb.add(*btns)

    # Nút chọn tất cả / bỏ tất cả
    all_selected = (set(selected_sites) == set(sites_list)) and len(selected_sites) > 0
    toggle_all_text = "❌ Bỏ chọn tất cả" if all_selected else "✅ Chọn tất cả"
    kb.row(InlineKeyboardButton(toggle_all_text, callback_data=f"reg_toggle_all_{house}"))

    # Nút tiếp tục (chỉ hiện khi đã chọn ≥1)
    if selected_sites:
        kb.row(InlineKeyboardButton(
            f"➡️ TIẾP TỤC ({len(selected_sites)} site)",
            callback_data=f"reg_continue_{house}",
        ))

    kb.row(InlineKeyboardButton("🔙 Quay lại", callback_data="reg_main"))
    return kb


def _kb_okvip_modes():
    """Sau khi user đã chọn site(s) OKVIP → chọn mode chung."""
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(
        "⚡ CHỈ TẠO TÀI KHOẢN",
        callback_data="reg_mode_simple",
    ))
    kb.row(InlineKeyboardButton(
        "🎁 TẠO + XÁC THỰC SĐT + NHẬN KM",
        callback_data="reg_mode_full",
    ))
    kb.row(InlineKeyboardButton("🔙 Chọn lại site", callback_data="reg_house_okvip"))
    return kb


# ─── HELPERS ──────────────────────────────────────────────
def _safe_edit(bot, chat_id, msg_id, text, markup=None):
    try:
        bot.edit_message_text(text, chat_id, msg_id,
                              reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(chat_id, text, reply_markup=markup,
                         parse_mode="HTML")


def _proxy_to_requests_url(proxy_str):
    p = (proxy_str or "").strip()
    if p.startswith("http://"):
        p = p[7:]
    elif p.startswith("https://"):
        p = p[8:]
    if not p:
        return None, "trống"

    if "@" in p:
        auth, hostport = p.rsplit("@", 1)
        if ":" not in auth:
            return None, "auth phải là user:pass"
        if ":" not in hostport:
            return None, "thiếu port"
        host, port_s = hostport.rsplit(":", 1)
        userpass = auth
    else:
        parts = p.split(":")
        if len(parts) == 2:
            host, port_s = parts
            userpass = ""
        elif len(parts) == 4:
            host, port_s, user, password = parts
            if not user or not password:
                return None, "thiếu user/pass"
            userpass = f"{user}:{password}"
        else:
            return None, "phải là ip:port hoặc ip:port:user:pass"

    if not host:
        return None, "thiếu host/ip"
    if not port_s.isdigit():
        return None, "port phải là số"
    port = int(port_s)
    if port < 1 or port > 65535:
        return None, "port ngoài khoảng 1-65535"

    auth_prefix = f"{userpass}@" if userpass else ""
    return f"http://{auth_prefix}{host}:{port}", None


def _validate_one_line(raw_text, house="okvip"):
    """Validate 1 dòng input.

    Cú pháp:
      • OKVIP: <code>proxy|TÊN|STK|BANK</code>          (4 phần)
      • KJC:   <code>proxy|TÊN|STK|BANK|SĐT</code>      (5 phần — thêm SĐT)

    Trả (ok, msg, dict). Khi sai, msg đã có HTML format.
    """
    if not raw_text:
        return False, "trống", None
    parts = [p.strip() for p in raw_text.split("|")]

    if house == "kjc":
        if len(parts) != 5:
            return False, (
                f"cần 5 phần <code>proxy|TÊN|STK|BANK|SĐT</code> "
                f"(có {len(parts)})"
            ), None
        proxy, name, stk, bank, phone = parts
    else:
        if len(parts) != 4:
            return False, (
                f"cần 4 phần <code>proxy|TÊN|STK|BANK</code> "
                f"(có {len(parts)})"
            ), None
        proxy, name, stk, bank = parts
        phone = ""  # OKVIP-FULL sẽ tự thuê SĐT, OKVIP-SIMPLE để random

    proxy_url, proxy_err = _proxy_to_requests_url(proxy)
    if proxy_err or not proxy_url:
        return False, (
            f"proxy sai ({proxy_err}); phải là <code>ip:port</code> "
            f"hoặc <code>ip:port:user:pass</code>"
        ), None

    # Validate tên
    if not name:
        return False, "tên trống", None
    if not re.match(r"^[A-Z\s]+$", name):
        return False, (
            "tên phải VIẾT HOA KHÔNG DẤU "
            "(vd <code>NGUYEN VAN A</code>)"
        ), None

    # Validate STK
    if not stk or not stk.isalnum():
        return False, "STK chỉ chứa chữ và số", None

    # Validate bank
    bank_u = bank.upper()
    if " " in bank_u:
        return False, (
            f"mã bank <code>{bank_u}</code> không hợp lệ; "
            "BANK phải viết liền không dấu cách "
            "(vd <code>MBBANK</code>, không nhập <code>MB BANK</code>)"
        ), None
    if bank_u not in VALID_BANKS:
        return False, f"mã bank <code>{bank_u}</code> không hợp lệ", None
    bank_api = bank_u
    if house == "kjc":
        bank_api = KJC_BANK_CODE_MAP.get(bank_u)
        if not bank_api:
            return False, (
                f"mã bank <code>{bank_u}</code> có trong list bot cũ nhưng "
                "KJC hiện chưa hỗ trợ bind bank mã này"
            ), None

    # Validate SĐT (chỉ KJC)
    if house == "kjc":
        if not stk.isdigit():
            return False, "STK KJC chỉ được chứa chữ số", None
        if len(stk) < 10 or len(stk) > 16:
            return False, (
                f"STK KJC độ dài {len(stk)} không hợp lệ "
                "(yêu cầu 10–16 chữ số; MBBANK nên dùng 14 số)"
            ), None
        if not phone:
            return False, "SĐT trống", None
        # Cho phép user nhập có dấu cách / +/-
        phone_clean = re.sub(r"[\s\+\-\.]", "", phone)
        if not phone_clean.isdigit():
            return False, "SĐT chỉ được chứa chữ số", None
        if len(phone_clean) < 9 or len(phone_clean) > 12:
            return False, (
                f"SĐT độ dài {len(phone_clean)} không hợp lệ "
                f"(yêu cầu 9–12 chữ số)"
            ), None
        phone = phone_clean

    return True, "", {
        "proxy": proxy,
        "realname": name,
        "stk": stk,
        "bank": bank_u,
        "bank_api": bank_api,
        "phone": phone,
    }


def _check_proxy_live(proxy_str, timeout=PROXY_CHECK_TIMEOUT):
    """Kiểm tra proxy có hoạt động không. Trả (ok, msg).

    Hỗ trợ format <code>ip:port</code> và <code>ip:port:user:pass</code>.
    """
    proxy_url, proxy_err = _proxy_to_requests_url(proxy_str)
    if proxy_err or not proxy_url:
        return False, proxy_err or "định dạng sai"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        resp = requests.get(PROXY_TEST_URL, proxies=proxies, timeout=timeout)
        if resp.status_code == 200:
            return True, "OK"
        return False, f"HTTP {resp.status_code}"
    except requests.exceptions.ConnectTimeout:
        return False, "timeout kết nối"
    except requests.exceptions.ReadTimeout:
        return False, "timeout đọc"
    except requests.exceptions.ProxyError as e:
        return False, f"proxy lỗi: {str(e)[:40]}"
    except Exception as e:
        return False, str(e)[:50]


def _batch_check_proxies(proxy_list, max_workers=PROXY_CHECK_WORKERS,
                         progress_callback=None):
    """Check song song nhiều proxy. Trả {proxy: (ok, msg)}.

    progress_callback(done, total): callable nếu muốn báo tiến độ live.
    """
    results = {}
    lock = threading.Lock()
    state = {"done": 0, "total": len(proxy_list)}

    def _check(proxy):
        ok, msg = _check_proxy_live(proxy)
        with lock:
            results[proxy] = (ok, msg)
            state["done"] += 1
            if progress_callback:
                try:
                    progress_callback(state["done"], state["total"])
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        list(exe.map(_check, proxy_list))
    return results


def _split_text_for_telegram(text, limit=TG_MAX_MESSAGE):
    """Cắt text thành các phần ≤ limit ký tự, ưu tiên cắt theo dòng."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.split("\n"):
        ln = len(line) + 1  # +1 cho \n
        if current_len + ln > limit and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = ln
        else:
            current.append(line)
            current_len += ln
    if current:
        chunks.append("\n".join(current))
    return chunks


# ─── PUBLIC: hiển thị menu chính ────────────────────────
def show_main(bot, chat_id, msg_id=None):
    kjc_price = _get_kjc_price()
    okvip_simple_price = _get_okvip_mode_price("simple")
    okvip_full_price = _get_okvip_mode_price("full")
    okvip_promo_price = _get_okvip_promo_price()
    text = (
        "🚀 <b>TẠO NICK</b>\n\n"
        "Chọn nhà bạn muốn đăng ký:\n\n"
        "🏠 <b>OKVIP</b> — 5 web: F168, C168, CM88, SC88, FLY88\n"
        f"   • Chỉ tạo: <b>{_fmt_money(okvip_simple_price)}/nick</b>\n"
        f"   • Xác thực + KM: <b>{_fmt_money(okvip_full_price)}/nick</b>\n"
        f"   • Xin lại/check KM: <b>{_fmt_money(okvip_promo_price)}/nick</b>\n"
        "🏠 <b>KJC</b> — 2 web: LLWIN, GG88 "
        f"(chỉ TẠO + BANK + PIN + NGÀY SINH) — "
        f"<b>1 nick = {_fmt_money(kjc_price)}</b>\n\n"
        f"⚙️ Tối đa <b>{REG_MAX_WORKERS} luồng</b> chạy song song, "
        f"job thừa sẽ vào hàng chờ."
    )
    if msg_id:
        _safe_edit(bot, chat_id, msg_id, text, _kb_house())
    else:
        bot.send_message(chat_id, text, reply_markup=_kb_house(),
                         parse_mode="HTML")


# ─── REGISTER HANDLERS VÀO BOT ────────────────────────
def register(bot, ADMIN_ID, user_allowed_fn, get_balance_fn=None,
             reserve_balance_fn=None, add_balance_fn=None,
             log_expense_fn=None, get_price_fn=None):
    global _REG_ADMIN_ID, _GET_BALANCE_FN, _RESERVE_BALANCE_FN
    global _ADD_BALANCE_FN, _LOG_EXPENSE_FN, _GET_PRICE_FN
    _REG_ADMIN_ID = ADMIN_ID
    _GET_BALANCE_FN = get_balance_fn
    _RESERVE_BALANCE_FN = reserve_balance_fn
    _ADD_BALANCE_FN = add_balance_fn
    _LOG_EXPENSE_FN = log_expense_fn
    _GET_PRICE_FN = get_price_fn

    def _is_admin(tid):
        return tid == ADMIN_ID

    @bot.message_handler(func=lambda m: m.text == "🚀 Tạo Nick")
    def _cmd_tao_nick(m):
        if not user_allowed_fn(m):
            return
        _clear_flow(m.chat.id)
        show_main(bot, m.chat.id)

    @bot.message_handler(commands=["check"])
    def _cmd_check(m):
        if not _is_admin(m.chat.id):
            return
        for chunk in _split_text_for_telegram(_format_process_check()):
            bot.send_message(m.chat.id, chunk, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("reg_"))
    def _cb_reg(call):
        data = call.data
        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        is_admin_user = _is_admin(call.from_user.id)

        if not is_admin_user and not user_allowed_fn(call.message):
            return

        if data == "reg_retry_okvip_km_last":
            _retry_last_okvip_pending_km(bot, call)
            return
        if data == "reg_export_okvip_km_last":
            _send_okvip_km_export(bot, call)
            return

        # ─── reg_main: về menu nhà
        if data == "reg_main":
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            _clear_flow(chat_id)
            show_main(bot, chat_id, msg_id)
            return

        # ─── reg_close: đóng menu
        if data == "reg_close":
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            _clear_flow(chat_id)
            try: bot.delete_message(chat_id, msg_id)
            except Exception: pass
            return

        # ─── reg_house_<house>: hiện list site multi-select
        if data.startswith("reg_house_"):
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            house = data.replace("reg_house_", "")
            # Reset flow: giữ house, reset sites + mode
            _clear_flow(chat_id)
            _set_flow(
                chat_id, house=house, sites=set(), mode=None,
                user_id=call.from_user.id,
                user_label=_label_from_tg_user(call.from_user, call.from_user.id),
            )
            _show_sites_menu(bot, chat_id, msg_id, house)
            return

        # ─── reg_toggle_<house>_<site>: toggle 1 site
        if data.startswith("reg_toggle_") and not data.startswith("reg_toggle_all_"):
            parts = data.split("_", 3)  # reg, toggle, house, site
            if len(parts) < 4:
                try: bot.answer_callback_query(call.id, "Lỗi data")
                except Exception: pass
                return
            house, site = parts[2], parts[3]
            _toggle_site(chat_id, site)
            selected = _get_selected_sites(chat_id)
            try:
                bot.answer_callback_query(
                    call.id,
                    ("✅ Đã chọn " if site in selected else "❌ Đã bỏ ")
                    + site.upper(),
                )
            except Exception:
                pass
            _show_sites_menu(bot, chat_id, msg_id, house)
            return

        # ─── reg_toggle_all_<house>: chọn/bỏ chọn tất cả
        if data.startswith("reg_toggle_all_"):
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            house = data.replace("reg_toggle_all_", "")
            sites_list = OKVIP_SITES if house == "okvip" else KJC_SITES
            _toggle_all_sites(chat_id, sites_list)
            _show_sites_menu(bot, chat_id, msg_id, house)
            return

        # ─── reg_continue_<house>: tiếp tục sau khi chọn site
        if data.startswith("reg_continue_"):
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            house = data.replace("reg_continue_", "")
            selected = _get_selected_sites(chat_id)
            if not selected:
                try:
                    bot.answer_callback_query(call.id, "⚠️ Chưa chọn site nào")
                except Exception:
                    pass
                return

            if house == "okvip":
                # Hiện chọn mode
                simple_price = _get_okvip_mode_price("simple")
                full_price = _get_okvip_mode_price("full")
                text = (
                    f"🏠 <b>OKVIP</b> — đã chọn <b>{len(selected)} site</b>: "
                    f"{', '.join(s.upper() for s in sorted(selected))}\n\n"
                    f"Chọn cách đăng ký (áp dụng cho TẤT CẢ site đã chọn):\n\n"
                    f"⚡ <b>CHỈ TẠO TÀI KHOẢN</b>\n"
                    f"   • Giá: <b>{_fmt_money(simple_price)}/nick</b>.\n"
                    f"   • Tạo nick nhanh qua API agent (~5–15s/nick).\n"
                    f"   • Không xác thực SĐT, không bind bank, không nhận KM.\n\n"
                    f"🎁 <b>TẠO + XÁC THỰC SĐT + NHẬN KM</b>\n"
                    f"   • Giá: <b>{_fmt_money(full_price)}/nick</b>.\n"
                    f"   • Đầy đủ flow (~30–90s/nick).\n"
                    f"   • Tốn SĐT ViOTP + captcha. Acc lên KM dùng được luôn.\n"
                    f"   • <b>Bank đã tồn tại/Trùng Bank không hoàn.</b>\n"
                    f"   • Chỉ hoàn khi không xác thực được SĐT/OTP sau nhiều lần thử.\n\n"
                    "🔒 Bot check proxy live trước; proxy OK mới trừ tiền."
                )
                _safe_edit(bot, chat_id, msg_id, text, _kb_okvip_modes())
            elif house == "kjc":
                # KJC chỉ có 1 mode = simple → vào input luôn
                _set_flow(chat_id, mode="simple")
                _ask_input_multi(bot, chat_id, msg_id)
            return

        # ─── reg_mode_<simple|full>: cho OKVIP, đã chọn site rồi
        if data.startswith("reg_mode_"):
            try: bot.answer_callback_query(call.id)
            except Exception: pass
            mode = data.replace("reg_mode_", "")
            _set_flow(chat_id, mode=mode)
            _ask_input_multi(bot, chat_id, msg_id)
            return


# ─── HIỂN THỊ SITES MENU (multi-select) ──────────────
def _show_sites_menu(bot, chat_id, msg_id, house):
    selected = _get_selected_sites(chat_id)
    sites_list = OKVIP_SITES if house == "okvip" else KJC_SITES

    title = "🏠 NHÀ OKVIP" if house == "okvip" else "🏠 NHÀ KJC"
    note = (
        "Bấm nút có ❌ để chọn, có ✅ để bỏ chọn.\n"
        "Có thể chọn nhiều site cùng lúc."
    )
    if house == "kjc":
        note += (
            "\n<i>KJC CHỈ TẠO + THÊM BANK + THÊM MÃ PIN + "
            "THÊM NGÀY SINH; không xác thực SĐT, không nhận KM.</i>"
        )
    else:
        note += (
            f"\n<i>OKVIP mở cho tất cả user: chỉ tạo "
            f"{_fmt_money(_get_okvip_mode_price('simple'))}/nick; "
            f"xác thực + KM {_fmt_money(_get_okvip_mode_price('full'))}/nick.\n"
            "Lưu ý: Bank đã tồn tại/Trùng Bank không hoàn; "
            "chỉ hoàn khi lỗi xác thực SĐT/OTP sau nhiều lần thử.</i>"
        )

    selected_str = (
        ", ".join(s.upper() for s in sorted(selected)) if selected
        else "<i>chưa chọn site nào</i>"
    )
    text = (
        f"{title}\n\n"
        f"{note}\n\n"
        f"🎯 Đã chọn: <b>{selected_str}</b>"
    )
    _safe_edit(bot, chat_id, msg_id, text, _kb_sites_multi(house, selected))


# ─── HỎI INPUT NHIỀU DÒNG ─────────────────────────────
def _ask_input_multi(bot, chat_id, msg_id):
    flow = _get_flow(chat_id)
    house = flow.get("house")
    sites = sorted(flow.get("sites") or set())
    mode = flow.get("mode") or "simple"

    mode_label = _mode_label(house, mode)
    syntax_line = _input_syntax_line(house)
    kjc_price = _get_kjc_price()
    if house == "kjc":
        example_text = (
            "<code>1.1.1.1:8080|NGUYEN VAN A|0123456789|MBBANK|0912345678</code>\n"
            "<code>2.2.2.2:8080|TRAN THI B|9876543210|VIETCOMB|0987654321</code>"
        )
        house_note = (
            f"🧩 <b>KJC chỉ TẠO + THÊM BANK + THÊM MÃ PIN ({PIN_DEFAULT}) "
            "+ THÊM NGÀY SINH</b>.\n"
            f"💵 Giá KJC: <b>1 nick = {_fmt_money(kjc_price)}</b>.\n"
            "🔒 Bot sẽ check proxy live trước, proxy OK mới trừ tiền; "
            "không đủ tiền sẽ dừng, không tạo nick.\n"
            "↩️ Nick thất bại được hoàn lại tiền khi kết thúc.\n"
            "Không xác thực SĐT, không nhận KM.\n"
            "STK KJC yêu cầu <b>10–16 số</b>; MBBANK nên dùng <b>14 số</b>.\n\n"
        )
    else:
        example_text = (
            "<code>1.1.1.1:8080|NGUYEN VAN A|0123456789|MBBANK</code>\n"
            "<code>2.2.2.2:8080|TRAN THI B|9876543210|VIETCOMB</code>"
        )
        okvip_price = _get_okvip_mode_price(mode)
        if mode == "simple":
            house_note = (
                f"⚡ <b>OKVIP CHỈ TẠO</b> — không xác thực SĐT, không nhận KM.\n"
                f"💵 Giá: <b>1 nick = {_fmt_money(okvip_price)}</b>.\n"
                "🔒 Bot sẽ check proxy live trước, proxy OK mới trừ tiền.\n"
                "⚠️ OKVIP chỉ hoàn khi lỗi xác thực SĐT/OTP sau nhiều lần thử; "
                "mode chỉ tạo không có bước xác thực SĐT nên lỗi tạo nick/site/bank sẽ không hoàn.\n\n"
            )
        else:
            house_note = (
                f"🎁 <b>OKVIP XÁC THỰC SĐT + NHẬN KM</b>.\n"
                f"💵 Giá: <b>1 nick = {_fmt_money(okvip_price)}</b>.\n"
                f"🔄 Xin lại/check KM đang chờ: <b>{_fmt_money(_get_okvip_promo_price())}/nick</b>.\n"
                "🔒 Bot sẽ check proxy live trước, proxy OK mới trừ tiền.\n"
                "⚠️ <b>Bank đã tồn tại/Trùng Bank không hoàn tiền.</b>\n"
                "↩️ Chỉ hoàn khi không xác thực được SĐT/OTP sau nhiều lần thử.\n\n"
            )

    text = (
        "📝 <b>NHẬP THÔNG TIN ĐĂNG KÝ</b>\n\n"
        f"🏠 Nhà: <b>{house.upper()}</b>\n"
        f"🌐 Site đã chọn ({len(sites)}): "
        f"<b>{', '.join(s.upper() for s in sites)}</b>\n"
        f"⚙️ Mode: <b>{mode_label}</b>\n\n"
        f"{house_note}"
        "📋 <b>Cú pháp 1 dòng</b>:\n"
        f"{syntax_line}\n\n"
        "🔢 <b>Bạn có thể nhập NHIỀU DÒNG</b> (mỗi dòng = 1 bộ thông tin).\n"
        f"Tổng nick sẽ tạo = (số dòng) × (số site đã chọn) = "
        f"<b>{len(sites)} × N dòng</b>.\n\n"
        "<b>Ví dụ nhập 2 dòng</b>:\n"
        f"{example_text}\n\n"
        "🏦 Mã <b>BANK</b> phải đúng list chuẩn; nếu sai bot sẽ báo danh sách hợp lệ.\n"
        "🔎 Bot sẽ kiểm tra proxy LIVE trước khi bắt đầu reg.\n\n"
        f"⚙️ Bot chạy tối đa <b>{REG_MAX_WORKERS} luồng song song</b>, "
        f"các job sau vào hàng chờ.\n"
        f"📏 Giới hạn: tối đa <b>{REG_MAX_LINES} dòng</b>, "
        f"<b>{REG_MAX_JOBS} job</b> tổng.\n\n"
        "Hoặc gõ /huy để huỷ."
    )
    cancel_kb = InlineKeyboardMarkup()
    cancel_kb.row(InlineKeyboardButton("🔙 Quay lại Menu Reg", callback_data="reg_main"))
    _safe_edit(bot, chat_id, msg_id, text, cancel_kb)
    bot.register_next_step_handler_by_chat_id(
        chat_id,
        lambda msg: _process_input(bot, msg),
    )


def _process_input(bot, message):
    chat_id = message.chat.id

    # Cancel
    text = (message.text or "").strip()
    if text.startswith("/"):
        bot.send_message(chat_id, "❎ Đã huỷ.")
        _clear_flow(chat_id)
        return

    flow = _get_flow(chat_id)
    house = flow.get("house")
    sites = sorted(flow.get("sites") or set())
    mode = flow.get("mode") or "simple"
    syntax_line = _input_syntax_line(house)
    if not house or not sites:
        bot.send_message(
            chat_id,
            "❌ Phiên đã hết. Bấm <b>🚀 Tạo Nick</b> để bắt đầu lại.",
            parse_mode="HTML",
        )
        return

    # Parse từng dòng
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        bot.send_message(chat_id, "❌ Không có dòng nào hợp lệ.")
        return

    if len(lines) > REG_MAX_LINES:
        bot.send_message(
            chat_id,
            f"❌ Quá nhiều dòng (<b>{len(lines)}</b>). "
            f"Tối đa <b>{REG_MAX_LINES}</b> dòng/lần.",
            parse_mode="HTML",
        )
        return

    parsed_rows = []
    errors = []
    for i, line in enumerate(lines, 1):
        ok, err_msg, parsed = _validate_one_line(line, house=house)
        if ok:
            parsed_rows.append(parsed)
        else:
            errors.append(f"   Dòng {i}: <b>{err_msg}</b>")

    if errors:
        has_bank_error = any("mã bank" in e for e in errors)
        err_text = (
            "❌ <b>SAI CÚ PHÁP</b>\n\n"
            + "\n".join(errors[:20])
            + ("\n..." if len(errors) > 20 else "")
            + "\n\n"
            + _syntax_guidance(house)
        )
        if has_bank_error:
            err_text += "\n\n" + _banks_help_text(house)
        bot.send_message(chat_id, err_text, parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(
            chat_id,
            lambda msg: _process_input(bot, msg),
        )
        return

    if not parsed_rows:
        bot.send_message(chat_id, "❌ Không có dòng hợp lệ.")
        return

    # Tạo cartesian product: jobs = sites × lines
    jobs = []
    for row in parsed_rows:
        for site in sites:
            jobs.append({
                "house": house,
                "site": site,
                "mode": mode,
                **row,
            })

    if len(jobs) > REG_MAX_JOBS:
        bot.send_message(
            chat_id,
            f"❌ Quá nhiều job tổng (<b>{len(jobs)}</b>). "
            f"Tối đa <b>{REG_MAX_JOBS}</b>.\n"
            f"Hãy giảm số dòng hoặc số site.",
            parse_mode="HTML",
        )
        return

    user_id = getattr(message.from_user, "id", chat_id) or chat_id
    user_label = flow.get("user_label") or _label_from_tg_user(message.from_user, user_id)
    unit_price = _get_reg_unit_price(house, mode)
    chargeable = unit_price > 0
    planned_cost = len(jobs) * unit_price if chargeable else 0
    reserved_cost = 0

    process_id = _create_process({
        "user_id": user_id,
        "user_label": _html_escape(user_label),
        "house": house,
        "mode": mode,
        "sites": sites,
        "input_lines": len(parsed_rows),
        "total": len(jobs),
        "chargeable": chargeable,
        "unit_price": unit_price,
        "planned_cost": planned_cost,
        "reserved_cost": reserved_cost,
        "refunded_cost": 0,
        "net_cost": 0,
        "note": "Đang check proxy",
    })
    _notify_admin_process_start(bot, process_id)

    unique_proxies = list(dict.fromkeys(row["proxy"] for row in parsed_rows))
    proxy_status = bot.send_message(
        chat_id,
        f"🔎 <b>ĐANG CHECK PROXY LIVE</b>\n\n"
        f"Proxy cần kiểm tra: <b>{len(unique_proxies)}</b>\n"
        f"Timeout mỗi proxy: <b>{PROXY_CHECK_TIMEOUT}s</b>\n\n"
        f"⏳ Đang chạy...",
        parse_mode="HTML",
    )
    last_proxy_update = {"t": 0}
    proxy_progress_lock = threading.Lock()

    def _proxy_progress(done, total_proxy):
        _update_process(
            process_id,
            status="checking_proxy",
            note=f"Đang check proxy {done}/{total_proxy}",
        )
        with proxy_progress_lock:
            now = time.time()
            if done < total_proxy and now - last_proxy_update["t"] < 1.5:
                return
            last_proxy_update["t"] = now
        try:
            bot.edit_message_text(
                f"🔎 <b>ĐANG CHECK PROXY LIVE</b>\n\n"
                f"Tiến độ: <b>{done}/{total_proxy}</b>",
                chat_id, proxy_status.message_id, parse_mode="HTML",
            )
        except Exception:
            pass

    proxy_results = _batch_check_proxies(
        unique_proxies,
        max_workers=PROXY_CHECK_WORKERS,
        progress_callback=_proxy_progress,
    )
    bad_proxies = [
        (p, msg)
        for p, (ok, msg) in proxy_results.items()
        if not ok
    ]
    if bad_proxies:
        if reserved_cost and _ADD_BALANCE_FN:
            try:
                _ADD_BALANCE_FN(user_id, reserved_cost)
            except Exception:
                pass
        bad_lines = [
            f"• <code>{_html_escape(p)}</code> — {_html_escape(msg)}"
            for p, msg in bad_proxies[:20]
        ]
        fail_text = (
            "❌ <b>PROXY KHÔNG LIVE / KHÔNG DÙNG ĐƯỢC</b>\n\n"
            + "\n".join(bad_lines)
            + ("\n..." if len(bad_proxies) > 20 else "")
            + "\n\nHãy thay proxy rồi gửi lại danh sách, hoặc /huy để huỷ."
        )
        if reserved_cost:
            fail_text += f"\n\n↩️ Đã hoàn lại: <b>{_fmt_money(reserved_cost)}</b>"
        try:
            bot.edit_message_text(
                fail_text, chat_id, proxy_status.message_id, parse_mode="HTML",
            )
        except Exception:
            bot.send_message(chat_id, fail_text, parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(
            chat_id,
            lambda msg: _process_input(bot, msg),
        )
        proc = _update_process(
            process_id,
            status="proxy_failed",
            state={
                "total": len(jobs), "done": 0, "ok": 0,
                "fail": len(jobs), "queued": 0, "running": 0,
            },
            refunded_cost=reserved_cost,
            net_cost=0,
            elapsed=time.time() - (_ACTIVE_PROCESSES.get(process_id, {}) or {}).get("created_at", time.time()),
            note="Proxy không live",
        )
        _notify_admin_process_end(bot, proc, fail_text)
        _remove_process(process_id)
        return

    try:
        bot.edit_message_text(
            f"✅ <b>PROXY LIVE OK</b>\n\n"
            f"Đã kiểm tra <b>{len(unique_proxies)}</b> proxy.\n"
            "Đang kiểm tra số dư...",
            chat_id, proxy_status.message_id, parse_mode="HTML",
        )
    except Exception:
        pass
    _update_process(process_id, status="charging", note="Proxy OK, đang kiểm tra số dư")

    if chargeable:
        if not (_GET_BALANCE_FN and _RESERVE_BALANCE_FN and _ADD_BALANCE_FN):
            text_fail = "❌ Chưa cấu hình thanh toán REG. Báo admin kiểm tra bot."
            bot.send_message(chat_id, text_fail, parse_mode="HTML")
            proc = _update_process(
                process_id,
                status="payment_config_error",
                state={
                    "total": len(jobs), "done": 0, "ok": 0,
                    "fail": len(jobs), "queued": 0, "running": 0,
                },
                note="Lỗi cấu hình thanh toán",
                elapsed=time.time() - (_ACTIVE_PROCESSES.get(process_id, {}) or {}).get("created_at", time.time()),
            )
            _notify_admin_process_end(bot, proc, text_fail)
            _remove_process(process_id)
            return

        balance = int(_GET_BALANCE_FN(user_id) or 0)
        if balance < planned_cost:
            billing_title = _billing_title(house, mode)
            text_fail = (
                "❌ <b>KHÔNG ĐỦ SỐ DƯ — KHÔNG TẠO NICK</b>\n\n"
                f"💵 Giá {billing_title}: <b>1 nick = {_fmt_money(unit_price)}</b>\n"
                f"📊 Số nick cần tạo: <b>{len(jobs)}</b>\n"
                f"🔒 Cần trừ trước khi chạy: <b>{_fmt_money(planned_cost)}</b>\n"
                f"💰 Số dư hiện tại: <b>{_fmt_money(balance)}</b>\n\n"
                "Vui lòng nạp thêm rồi thử lại."
            )
            bot.send_message(chat_id, text_fail, parse_mode="HTML")
            proc = _update_process(
                process_id,
                status="insufficient_balance",
                state={
                    "total": len(jobs), "done": 0, "ok": 0,
                    "fail": len(jobs), "queued": 0, "running": 0,
                },
                note="Không đủ số dư, chưa chạy reg",
                elapsed=time.time() - (_ACTIVE_PROCESSES.get(process_id, {}) or {}).get("created_at", time.time()),
            )
            _notify_admin_process_end(bot, proc, text_fail)
            _remove_process(process_id)
            return

        ok_charge, new_balance = _RESERVE_BALANCE_FN(user_id, planned_cost)
        if not ok_charge:
            text_fail = (
                "❌ <b>KHÔNG ĐỦ SỐ DƯ — KHÔNG TẠO NICK</b>\n\n"
                f"🔒 Cần trừ: <b>{_fmt_money(planned_cost)}</b>\n"
                f"💰 Số dư hiện tại: <b>{_fmt_money(new_balance)}</b>"
            )
            bot.send_message(chat_id, text_fail, parse_mode="HTML")
            proc = _update_process(
                process_id,
                status="insufficient_balance",
                state={
                    "total": len(jobs), "done": 0, "ok": 0,
                    "fail": len(jobs), "queued": 0, "running": 0,
                },
                note="Không đủ số dư, chưa chạy reg",
                elapsed=time.time() - (_ACTIVE_PROCESSES.get(process_id, {}) or {}).get("created_at", time.time()),
            )
            _notify_admin_process_end(bot, proc, text_fail)
            _remove_process(process_id)
            return

        reserved_cost = planned_cost
        _update_process(
            process_id,
            reserved_cost=reserved_cost,
            status="starting",
            note="Đã trừ tiền, chuẩn bị reg",
        )
        billing_title = _billing_title(house, mode)
        bot.send_message(
            chat_id,
            f"🔒 <b>ĐÃ TRỪ/TẠM GIỮ TIỀN REG {billing_title}</b>\n\n"
            f"📊 Số nick: <b>{len(jobs)}</b>\n"
            f"💵 Giá: <b>1 nick = {_fmt_money(unit_price)}</b>\n"
            f"🔒 Đã trừ: <b>{_fmt_money(reserved_cost)}</b>\n"
            f"💰 Số dư còn lại: <b>{_fmt_money(new_balance)}</b>\n\n"
            "Nick thất bại sẽ được hoàn tiền khi tiến trình kết thúc.",
            parse_mode="HTML",
        )
    else:
        _update_process(process_id, status="starting", note="Proxy OK, chuẩn bị reg")

    _clear_flow(chat_id)

    # Khởi động batch
    mode_label = _mode_label(house, mode)
    sites_str = ", ".join(s.upper() for s in sites)
    sent = bot.send_message(
        chat_id,
        f"🚀 <b>BẮT ĐẦU REG SONG SONG</b>\n\n"
        f"🏠 Nhà: <b>{house.upper()}</b>\n"
        f"🌐 Sites ({len(sites)}): <b>{sites_str}</b>\n"
        f"📝 Số dòng input: <b>{len(parsed_rows)}</b>\n"
        f"📊 Tổng nick: <b>{len(jobs)}</b>\n"
        f"⚙️ Mode: <b>{mode_label}</b>\n"
        f"⚡ Tối đa <b>{REG_MAX_WORKERS}</b> luồng song song\n\n"
        "⏳ Khởi tạo batch...",
        parse_mode="HTML",
    )
    threading.Thread(
        target=_run_batch,
        args=(
            bot, chat_id, sent.message_id, jobs, parsed_rows, sites,
            mode, house, process_id, {
                "user_id": user_id,
                "chargeable": chargeable,
                "unit_price": unit_price,
                "reserved_cost": reserved_cost,
            },
        ),
        daemon=True,
    ).start()


# ─── WORKER + BATCH RUNNER ────────────────────────────
def _run_one_subprocess(args):
    """Chạy runner.py qua subprocess. Trả về dict kết quả."""
    try:
        proc = subprocess.run(
            [sys.executable, RUNNER_PATH],
            input=json.dumps(args, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=REG_TIMEOUT_PER_JOB,
            encoding="utf-8",
        )
        stdout = (proc.stdout or "").strip()
        if not stdout:
            return {
                "ok": False,
                "msg": f"Runner empty. Stderr: {(proc.stderr or '')[:200]}",
            }
        last_line = stdout.split("\n")[-1]
        try:
            return json.loads(last_line)
        except Exception as e:
            return {
                "ok": False,
                "msg": f"Parse JSON: {e}. Last line: {last_line[:200]}",
            }
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": f"Timeout (>{REG_TIMEOUT_PER_JOB}s)"}
    except Exception as e:
        return {"ok": False, "msg": f"Lỗi runner: {e}"}


def _run_batch(bot, chat_id, status_msg_id, jobs, parsed_rows, sites,
               mode, house, process_id=None, billing_ctx=None):
    """Chạy batch jobs với ThreadPoolExecutor 50 luồng. Live progress + summary."""
    billing_ctx = billing_ctx or {}
    total = len(jobs)
    results = [None] * total  # giữ thứ tự
    state = {"total": total, "done": 0, "ok": 0, "fail": 0, "queued": total, "running": 0}
    lock = threading.Lock()

    def _work(idx, job):
        with lock:
            state["queued"] = max(0, state["queued"] - 1)
            state["running"] += 1
            current_state = dict(state)
        if process_id:
            _worker_start(process_id, idx, job)
            _update_process(process_id, status="running", state=current_state, note="Đang reg")
        result = _run_one_subprocess(job)
        with lock:
            results[idx] = {"job": job, "result": result}
            state["done"] += 1
            state["running"] = max(0, state["running"] - 1)
            if result.get("ok"):
                state["ok"] += 1
            else:
                state["fail"] += 1
            current_state = dict(state)
        if process_id:
            _worker_done(process_id, idx)
            _update_process(process_id, status="running", state=current_state, note="Đang reg")

    # Submit jobs
    pool = ThreadPoolExecutor(max_workers=REG_MAX_WORKERS)
    futures = [pool.submit(_work, i, job) for i, job in enumerate(jobs)]

    # Live progress: edit status_msg mỗi PROGRESS_UPDATE_INTERVAL giây
    start = time.time()
    last_update = 0
    mode_label = _mode_label(house, mode)

    while True:
        with lock:
            done = state["done"]
            ok = state["ok"]
            fail = state["fail"]
            running = state["running"]
            queued = state["queued"]
        if done >= total:
            break
        now = time.time()
        if now - last_update >= PROGRESS_UPDATE_INTERVAL:
            elapsed = now - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0

            try:
                bot.edit_message_text(
                    f"🚀 <b>ĐANG REG…</b>\n\n"
                    f"🏠 {house.upper()} | ⚙️ {mode_label}\n"
                    f"🌐 {len(sites)} site × {len(parsed_rows)} dòng = "
                    f"<b>{total}</b> nick\n\n"
                    f"📊 Tiến độ: <b>{done}/{total}</b>\n"
                    f"   ✅ OK: <b>{ok}</b>\n"
                    f"   ❌ Fail: <b>{fail}</b>\n"
                    f"   🏃 Đang chạy: <b>{running}</b>\n"
                    f"   ⏳ Hàng chờ: <b>{queued}</b>\n\n"
                    f"⏱ Đã chạy: <b>{elapsed:.1f}s</b> "
                    f"(~{rate:.2f} nick/s, ETA <b>{eta:.0f}s</b>)",
                    chat_id, status_msg_id, parse_mode="HTML",
                )
                last_update = now
            except Exception:
                pass
        time.sleep(0.5)

    pool.shutdown(wait=True)
    elapsed = time.time() - start

    # Xoá tin progress
    try:
        bot.delete_message(chat_id, status_msg_id)
    except Exception:
        pass

    # Build summary
    ok_lines = []
    fail_lines = []
    okvip_km_records = []
    failure_refunds = []
    bank_duplicate_count = 0
    okvip_sms_refund_count = 0
    no_refund_count = 0
    summary_unit_price = int(billing_ctx.get("unit_price") or _get_reg_unit_price(house, mode))
    for r in results:
        if r is None:
            continue
        job = r["job"]
        result = r["result"]
        site = job["site"].upper()
        if result.get("ok"):
            uname = result.get("username", "?")
            pwd = result.get("password", "?")
            phone = result.get("phone", "")
            km = result.get("km_label", "")
            line = f"✅ <b>{site}</b> | <code>{uname}</code> | <code>{pwd}</code>"
            if phone:
                line += f" | 📞 {phone}"
            if house == "okvip" and mode == "full":
                okvip_km_records.append({
                    "site": job.get("site", ""),
                    "username": uname,
                    "password": pwd,
                    "phone": phone,
                    "proxy": job.get("proxy", ""),
                    "realname": job.get("realname", ""),
                    "pin": result.get("pin") or job.get("pin") or PIN_DEFAULT,
                    "stk": job.get("stk", ""),
                    "bank": job.get("bank", ""),
                    "km_label": km,
                    "km_msg": result.get("km_msg", ""),
                })
            if house == "kjc":
                pin = result.get("pin") or job.get("pin") or PIN_DEFAULT
                dob = result.get("dob") or ""
                bank = result.get("bank") or job.get("bank", "")
                stk = result.get("stk") or job.get("stk", "")
                line += f" | PIN <code>{pin}</code>"
                if dob:
                    line += f" | DOB <code>{dob}</code>"
                if bank or stk:
                    line += f" | 🏦 <code>{bank}</code> <code>{stk}</code>"
            if km and km != "ERROR":
                line += f" | 🎁 {km}"
            ok_lines.append(line)
        else:
            err_msg = result.get("msg") or ""
            short_err = _short_error_message(err_msg)
            if short_err == "Trùng Bank":
                bank_duplicate_count += 1
            refund_amount = summary_unit_price if _is_okvip_refundable_error(err_msg, short_err) else 0
            if house == "kjc":
                refund_amount = summary_unit_price // 2 if short_err == "Trùng Bank" else summary_unit_price
            elif house == "okvip" and mode == "full":
                if refund_amount:
                    okvip_sms_refund_count += 1
                elif short_err != "Trùng Bank":
                    no_refund_count += 1
            elif house == "okvip":
                if short_err != "Trùng Bank":
                    no_refund_count += 1
            failure_refunds.append({
                "house": house,
                "mode": mode,
                "short_err": short_err,
                "raw_msg": err_msg,
                "refund": refund_amount,
            })
            err = _html_escape(short_err)
            realname = _html_escape(job.get("realname", "?")[:15])
            if house == "kjc" and (result.get("steps") or {}).get("register") is True:
                uname = result.get("username", "?")
                pwd = result.get("password", "?")
                pin = result.get("pin") or job.get("pin") or PIN_DEFAULT
                dob = result.get("dob") or ""
                bank = result.get("bank") or job.get("bank", "")
                stk = result.get("stk") or job.get("stk", "")
                line = (
                    f"⚠️ <b>{site}</b> | <code>{uname}</code> | "
                    f"<code>{pwd}</code> | PIN <code>{pin}</code>"
                )
                if dob:
                    line += f" | DOB <code>{dob}</code>"
                if bank or stk:
                    line += f" | 🏦 <code>{bank}</code> <code>{stk}</code>"
                line += f" | {err}"
            else:
                line = f"❌ <b>{site}</b> | {realname} | {err}"
            fail_lines.append(line)

    header = (
        "🏁 <b>BATCH HOÀN TẤT</b>\n\n"
        f"🏠 Nhà: <b>{house.upper()}</b> | "
        f"⚙️ <b>{mode_label}</b>\n"
        f"📊 Tổng: <b>{total}</b> | "
        f"✅ OK: <b>{state['ok']}</b> | "
        f"❌ Fail: <b>{state['fail']}</b>\n"
        f"⏱ Thời gian: <b>{elapsed:.1f}s</b> "
        f"(~{total/elapsed if elapsed>0 else 0:.2f} nick/s)\n"
        f"━━━━━━━━━━━━━"
    )

    full_text = header
    if ok_lines:
        full_text += "\n\n✅ <b>THÀNH CÔNG</b>:\n" + "\n".join(ok_lines)
    if fail_lines:
        full_text += "\n\n❌ <b>THẤT BẠI</b>:\n" + "\n".join(fail_lines)

    summary_markup = None
    if house == "okvip" and mode == "full" and okvip_km_records:
        _save_last_okvip_km_results(chat_id, okvip_km_records)
        pending_count = sum(
            1 for r in okvip_km_records
            if _km_text_pending(r.get("km_label"), r.get("km_msg"))
        )
        if pending_count > 0:
            full_text += (
                f"\n\n🎁 Có <b>{pending_count}</b> acc KM <b>ĐANG CHỜ</b>. "
                "Bấm nút dưới để xin lại/check tiếp khuyến mãi."
            )
            summary_markup = InlineKeyboardMarkup()
            summary_markup.add(InlineKeyboardButton(
                "🔄 Check KM các acc Đang chờ",
                callback_data="reg_retry_okvip_km_last",
            ))
            summary_markup.add(InlineKeyboardButton(
                "📥 Tải danh sách acc",
                callback_data="reg_export_okvip_km_last",
            ))

    refunded_cost = 0
    net_cost = 0
    if billing_ctx.get("chargeable"):
        user_id = billing_ctx.get("user_id")
        reserved_cost = int(billing_ctx.get("reserved_cost") or 0)
        unit_price = int(billing_ctx.get("unit_price") or _get_reg_unit_price(house, mode))
        billing_title = _billing_title(house, mode)
        success_cost = state["ok"] * unit_price

        refunded_cost = sum(int(x.get("refund") or 0) for x in failure_refunds)
        net_cost = max(0, reserved_cost - refunded_cost)
        
        if refunded_cost and _ADD_BALANCE_FN:
            try:
                _ADD_BALANCE_FN(user_id, refunded_cost)
            except Exception:
                pass
        if net_cost and _LOG_EXPENSE_FN:
            try:
                _LOG_EXPENSE_FN(
                    user_id, net_cost, f"reg_{house}",
                    f"{process_id}: {state['ok']}/{total} acc {billing_title} OK",
                )
            except Exception:
                pass
        full_text += (
            f"\n\n💵 <b>THANH TOÁN {billing_title}</b>:\n"
            f"• Giá: <b>1 nick = {_fmt_money(unit_price)}</b>\n"
            f"• Thành công tính tiền: <b>{state['ok']}</b> acc = "
            f"<b>{_fmt_money(success_cost)}</b>\n"
        )
        if bank_duplicate_count > 0:
            if house == "kjc":
                full_text += (
                    f"• Trùng Bank hoàn 50%: <b>{bank_duplicate_count}</b> acc = "
                    f"<b>{_fmt_money(bank_duplicate_count * unit_price // 2)}</b>\n"
                )
            else:
                full_text += (
                    f"• Trùng Bank/Bank tồn tại không hoàn: <b>{bank_duplicate_count}</b> acc = "
                    f"<b>{_fmt_money(0)}</b>\n"
                )
        if house == "okvip":
            if okvip_sms_refund_count > 0:
                full_text += (
                    f"• Lỗi xác thực SĐT/OTP hoàn 100%: <b>{okvip_sms_refund_count}</b> acc = "
                    f"<b>{_fmt_money(okvip_sms_refund_count * unit_price)}</b>\n"
                )
            if no_refund_count > 0:
                full_text += (
                    f"• Lỗi không thuộc diện hoàn: <b>{no_refund_count}</b> acc = "
                    f"<b>{_fmt_money(0)}</b>\n"
                )
        else:
            other_fail_count = max(0, state["fail"] - bank_duplicate_count)
            if other_fail_count > 0:
                full_text += (
                    f"• Lỗi khác hoàn 100%: <b>{other_fail_count}</b> acc = "
                    f"<b>{_fmt_money(other_fail_count * unit_price)}</b>\n"
                )
        full_text += (
            f"• Tổng hoàn lại: <b>{state['fail']}</b> acc = <b>{_fmt_money(refunded_cost)}</b>\n"
            f"• Thực thu: <b>{_fmt_money(net_cost)}</b>"
        )

    proc = None
    if process_id:
        proc = _update_process(
            process_id,
            status="finished",
            state=dict(state),
            active_workers={},
            elapsed=elapsed,
            refunded_cost=refunded_cost,
            net_cost=net_cost,
            note="Hoàn tất",
        )

    # Cắt nhỏ nếu vượt giới hạn Telegram
    chunks = _split_text_for_telegram(full_text)
    for idx, chunk in enumerate(chunks):
        reply_markup = summary_markup if idx == len(chunks) - 1 else None
        try:
            bot.send_message(
                chat_id,
                chunk,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as e:
            # Nếu HTML fail, gửi plain text
            try:
                bot.send_message(chat_id, chunk, reply_markup=reply_markup)
            except Exception:
                bot.send_message(chat_id, f"(Lỗi gửi summary: {e})")
        time.sleep(0.1)

    if process_id:
        _notify_admin_process_end(bot, proc, full_text)
        _remove_process(process_id)
