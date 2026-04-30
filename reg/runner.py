# -*- coding: utf-8 -*-
"""
NEWBOT — Reg Runner (subprocess CLI)
=====================================
Chạy như 1 Python script độc lập. Đọc JSON args từ stdin, in JSON kết quả ra
stdout (dòng cuối là kết quả; các dòng trước có thể là log).

Stderr: chỉ chứa log debug (parent process bỏ qua).

USAGE từ Python:
    proc = subprocess.run(
        [sys.executable, "reg/runner.py"],
        input=json.dumps({
            "house": "okvip",
            "site":  "f168",
            "proxy": "1.1.1.1:8080",
            "realname": "NGUYEN VAN A",
            "stk":   "0123456789",
            "bank":  "MBBANK",
        }),
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    result = json.loads(proc.stdout.strip().split("\\n")[-1])

Hỗ trợ nhà:
  • house="okvip"  → REG_API_SERVER/autoreg_v2.py    (5 site: f168/c168/cm88/sc88/fly88)
  • house="kjc"    → REG_API_SERVER/Reg Nha KJC/core_logic.py (7 site + thêm llwin/gg88)
"""
import os
import sys
import json
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))         # NEWBOT/reg/
NEWBOT_DIR = os.path.dirname(HERE)                        # NEWBOT/
THUANBOT_DIR = os.path.dirname(NEWBOT_DIR)                # THUANBOT/
REG_API_SERVER_DIR = os.path.join(THUANBOT_DIR, "REG_API_SERVER")
KJC_DIR = os.path.join(REG_API_SERVER_DIR, "Reg Nha KJC")


def _eprint(*args, **kwargs):
    """Print to stderr (debug log). Bot parent process sẽ bỏ qua."""
    print(*args, file=sys.stderr, **kwargs)


def parse_proxy(proxy_str):
    """'ip:port' hoặc 'ip:port:user:pass' → dict {http, https}.

    Hỗ trợ cả format có sẵn user:pass@ip:port.
    """
    if not proxy_str:
        return None
    p = proxy_str.strip()
    if p.startswith("http://"):
        p = p[7:]
    elif p.startswith("https://"):
        p = p[8:]
    if "@" not in p:
        parts = p.split(":")
        if len(parts) == 4:  # ip:port:user:pass → user:pass@ip:port
            p = f"{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return {"http": f"http://{p}", "https": f"http://{p}"}


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


def run_okvip(args):
    """Reg 1 nick cho nhà OKVIP. Hỗ trợ 2 mode:

      • mode="simple"  — CHỈ TẠO TÀI KHOẢN (HTTP POST tới agent API,
                          không xác thực SĐT, không liên kết bank, không nhận KM).
      • mode="full"    — TẠO + XÁC THỰC SĐT + LIÊN KẾT BANK + ĐĂNG KÝ KM.
                          Dùng autoreg_v2.full_flow() rồi promo_km._do_km_check().
    """
    mode = (args.get("mode") or "full").lower()
    if mode == "simple":
        return _run_okvip_simple(args)
    return _run_okvip_full(args)


def _run_okvip_simple(args):
    """Mode CHỈ TẠO: dùng register native của autoreg_v2, dừng sau bước tạo acc."""
    if REG_API_SERVER_DIR not in sys.path:
        sys.path.insert(0, REG_API_SERVER_DIR)

    import autoreg_v2 as ar

    site = args["site"].lower()
    profile = ar.SITE_PROFILES.get(site)
    if not profile:
        return {"ok": False, "msg": f"Site {site} không hỗ trợ"}

    username = args.get("username") or ar.random_username()
    password = args.get("password") or ar.random_password(username)
    realname = args["realname"].upper()
    phone = ar.random_phone() if hasattr(ar, "random_phone") else ""
    proxy_dict = parse_proxy(args.get("proxy"))

    try:
        try:
            from G_token_Thuan_Tuy_python import gen_random_device
            dev = gen_random_device()
        except Exception as e:
            _eprint(f"[OKVIP-SIMPLE] gen_random_device fail: {e}")
            dev = None

        site_domain = profile["site_domains"][0]
        device_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        aes_key = ar.f168_aes_key(token)
        gee_token = ""
        try:
            import G_token_Thuan_Tuy_python
            gee_token = G_token_Thuan_Tuy_python.generate(
                device=dev,
                proxy_dict=proxy_dict,
                site_domain=site_domain,
            )
        except Exception as e:
            _eprint(f"[OKVIP-SIMPLE] geetoken fail: {e}")

        imp = dev.get("impersonate", "chrome120") if isinstance(dev, dict) else "chrome120"
        session = ar.safe_session(imp, proxy_dict)
        if isinstance(dev, dict):
            session.headers.update({
                "User-Agent": dev.get("ua", ""),
                "sec-ch-ua": dev.get("sec_ch_ua", ""),
                "sec-ch-ua-platform": dev.get("sec_ch_ua_platform", ""),
            })

        api_headers = ar.make_base_headers(
            site_domain, device_id, token, dev=dev, profile=profile
        )
        ar._warmup_acw_tc(session, site_domain, profile=profile, dev=dev)
        reg_ok, api_base, reg_data = ar.register_account(
            session, username, password, realname, phone,
            site_domain, device_id, token, aes_key, api_headers,
            gee_token=gee_token, dev=dev, profile=profile,
        )
        if reg_ok:
            return {
                "ok": True,
                "house": "okvip",
                "site": site,
                "mode": "simple",
                "username": username,
                "password": password,
                "realname": realname,
                "phone": phone,
                "msg": "Đăng ký nhanh thành công (chỉ tạo)",
                "raw": reg_data if isinstance(reg_data, dict) else {},
            }
        if isinstance(reg_data, dict):
            err_msg = (
                reg_data.get("error")
                or reg_data.get("msg")
                or reg_data.get("message")
                or str(reg_data)[:200]
            )
        else:
            err_msg = str(reg_data)[:200]
        return {
            "ok": False,
            "house": "okvip",
            "site": site,
            "mode": "simple",
            "username": username,
            "password": password,
            "phone": phone,
            "msg": err_msg or "Đăng ký thất bại",
        }
    except Exception as e:
        return {
            "ok": False,
            "house": "okvip",
            "site": site,
            "mode": "simple",
            "username": username,
            "password": password,
            "msg": f"Lỗi kết nối: {e}",
        }


def _run_okvip_full(args):
    """Mode FULL: Tạo + Xác thực SĐT + Bind Bank + Nhận KM."""
    if REG_API_SERVER_DIR not in sys.path:
        sys.path.insert(0, REG_API_SERVER_DIR)

    import autoreg_v2 as ar
    import config as ar_config

    site = args["site"].lower()
    profile = ar.SITE_PROFILES.get(site)
    if not profile:
        return {"ok": False, "msg": f"Site {site} không hỗ trợ ở nhà OKVIP"}

    username = args.get("username") or ar.random_username()
    password = args.get("password") or ar.random_password(username)
    realname = args["realname"].upper()
    stk = args["stk"]
    bank = args["bank"]
    pin = args.get("pin", "111222")
    proxy_dict = parse_proxy(args.get("proxy"))

    # Thuê SĐT
    _eprint(f"[OKVIP-FULL] Thuê SĐT từ ViOTP service {ar_config.VIOTP_SERVICE_ID}…")
    phone, request_id, rent_msg = ar.viotp_rent_phone(
        ar_config.VIOTP_TOKEN, ar_config.VIOTP_SERVICE_ID
    )
    if not phone:
        return {"ok": False, "msg": f"Không thuê được SĐT: {rent_msg}"}
    _eprint(f"[OKVIP-FULL] Đã thuê SĐT: {phone} (req={request_id})")

    try:
        from G_token_Thuan_Tuy_python import gen_random_device
        dev = gen_random_device()
    except Exception as e:
        _eprint(f"[OKVIP-FULL] gen_random_device fail: {e}")
        dev = None

    _eprint(f"[OKVIP-FULL] full_flow: site={site} user={username} phone={phone}")
    ok, res = ar.full_flow(
        username=username,
        password=password,
        realname=realname,
        phone=phone,
        proxy_dict=proxy_dict,
        site_domain=profile["site_domains"][0],
        pin=pin,
        bank_name=bank,
        bank_number=stk,
        gee_token="",
        dev=dev,
        viotp_token=ar_config.VIOTP_TOKEN,
        viotp_request_id=request_id,
        viotp_service_id=ar_config.VIOTP_SERVICE_ID,
        profile=profile,
        autocaptcha_key=ar_config.AUTOCAPTCHA_KEY,
        captcha_provider=ar_config.CAPTCHA_PROVIDER,
    )

    if not (ok and isinstance(res, dict)):
        err_msg = ""
        if isinstance(res, dict):
            err_msg = res.get("error") or res.get("msg") or str(res)[:200]
        else:
            err_msg = str(res)[:200]
        return {
            "ok": False,
            "house": "okvip",
            "site": site,
            "mode": "full",
            "username": username,
            "password": password,
            "phone": phone,
            "msg": err_msg or "Lỗi không rõ",
        }

    # Đăng ký + xác thực OK → đăng ký KM
    km_label = ""
    km_msg = ""
    try:
        from promo_km import _do_km_check, KM_SITES
        if site in KM_SITES:
            _eprint(f"[OKVIP-FULL] Đăng ký KM cho {username}…")
            km_label, km_msg, _ = _do_km_check(
                user=username,
                proxy_str=args.get("proxy") or "",
                config=KM_SITES[site],
            )
            _eprint(f"[OKVIP-FULL] KM result: {km_label}")
    except Exception as e:
        _eprint(f"[OKVIP-FULL] KM fail (bỏ qua): {e}")
        km_label = "ERROR"
        km_msg = str(e)

    return {
        "ok": True,
        "house": "okvip",
        "site": site,
        "mode": "full",
        "username": username,
        "password": password,
        "phone": res.get("phone", phone),
        "realname": realname,
        "stk": stk,
        "bank": bank,
        "msg": "Đăng ký + xác thực + KM thành công",
        "km_label": km_label,
        "km_msg": km_msg,
        "raw": {k: v for k, v in res.items() if not k.startswith("_")},
    }


def run_kjc(args):
    """Reg 1 nick cho nhà KJC (chỉ 2 site: llwin, gg88).

    KJC khác OKVIP: KHÔNG xác minh SMS (skip_sms=True), KHÔNG thuê SĐT từ ViOTP,
    dùng SĐT user nhập để điền form, rồi set PIN + DOB + bind bank.
    """
    if KJC_DIR not in sys.path:
        sys.path.insert(0, KJC_DIR)

    import core_logic as cl
    import config as kjc_config

    site = args["site"].lower()
    profile = cl.SITE_PROFILES.get(site)
    if not profile:
        return {"ok": False, "msg": f"Site {site} không hỗ trợ ở nhà KJC"}
    if site not in ("llwin", "gg88"):
        return {
            "ok": False,
            "msg": f"Nhà KJC chỉ hỗ trợ llwin và gg88, không hỗ trợ {site}",
        }

    username = args.get("username") or cl.random_username()
    password = args.get("password") or cl.random_password(username)
    realname = args["realname"].upper()
    stk = args["stk"]
    bank_input = args["bank"].upper()
    bank = (args.get("bank_api") or KJC_BANK_CODE_MAP.get(bank_input) or bank_input).upper()
    pin = args.get("pin", "111222")
    proxy_dict = parse_proxy(args.get("proxy"))
    phone = (args.get("phone") or "").strip() or cl.random_phone()

    try:
        from G_token_Thuan_Tuy_python import gen_random_device
        dev = gen_random_device()
    except Exception as e:
        _eprint(f"[KJC] gen_random_device fail: {e}")
        dev = None

    bank_debug = {}
    orig_bind_bank_card = getattr(cl, "bind_bank_card", None)
    if orig_bind_bank_card:
        def _bind_bank_card_capture(*a, **kw):
            bind_res, bind_bank, bind_number = orig_bind_bank_card(*a, **kw)
            bank_debug["bind_res"] = bind_res
            bank_debug["bank"] = bind_bank
            bank_debug["number"] = bind_number
            return bind_res, bind_bank, bind_number
        cl.bind_bank_card = _bind_bank_card_capture

    _eprint(
        f"[KJC] full_flow: site={site} user={username} phone={phone} "
        f"bank={bank_input}->{bank} (skip_sms=True)"
    )
    ok, res = cl.full_flow(
        username=username,
        password=password,
        realname=realname,
        phone=phone,
        proxy_dict=proxy_dict,
        site_domain=profile["site_domains"][0],
        pin=pin,
        bank_name=bank,
        bank_number=stk,
        gee_token="",
        dev=dev,
        viotp_token=None,
        viotp_request_id=None,
        viotp_service_id=None,
        profile=profile,
        anticaptcha_key=kjc_config.ANTICAPTCHA_KEY,
        captcha_provider=kjc_config.CAPTCHA_PROVIDER,
        skip_sms=True,   # ← KJC luôn bỏ qua xác minh SMS
        skip_bank=False,
        skip_dob=False,
        otp_callback=None,
    )
    if orig_bind_bank_card:
        cl.bind_bank_card = orig_bind_bank_card

    if ok and isinstance(res, dict):
        steps = res.get("steps") or {}
        missing_steps = []
        if steps.get("set_pin") is not True:
            missing_steps.append("PIN")
        if steps.get("set_dob") is not True:
            missing_steps.append("DOB")
        if steps.get("bind_bank") is not True:
            missing_steps.append("BANK")
        common = {
            "house": "kjc",
            "site": site,
            "username": res.get("username", username),
            "password": res.get("password", password),
            "phone": res.get("phone", phone),
            "realname": res.get("realname", realname),
            "stk": res.get("bank_number") or stk,
            "bank": bank_input,
            "bank_api": res.get("bank_name") or bank,
            "pin": res.get("pin") or pin,
            "dob": res.get("dob", ""),
            "steps": steps,
            "bank_debug": bank_debug,
            "raw": {k: v for k, v in res.items() if not k.startswith("_")},
        }
        if missing_steps:
            bind_res = bank_debug.get("bind_res") or {}
            bind_msg = ""
            if isinstance(bind_res, dict):
                msg = bind_res.get("msg") or bind_res.get("message") or ""
                code = bind_res.get("code")
                if msg or code is not None:
                    bind_msg = f" (code={code}, msg={msg})"
            return {
                "ok": False,
                **common,
                "msg": "Tạo acc OK nhưng lỗi bước: "
                       + ", ".join(missing_steps) + bind_msg,
            }
        return {
            "ok": True,
            **common,
            "msg": "Đăng ký + bank + PIN + DOB thành công",
        }
    err_msg = ""
    if isinstance(res, dict):
        err_msg = res.get("error") or res.get("msg") or str(res)[:200]
    else:
        err_msg = str(res)[:200]
    return {
        "ok": False,
        "house": "kjc",
        "site": site,
        "username": username,
        "password": password,
        "phone": phone,
        "realname": realname,
        "stk": stk,
        "bank": bank_input,
        "bank_api": bank,
        "pin": pin,
        "bank_debug": bank_debug,
        "msg": err_msg or "Lỗi không rõ",
    }


def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"ok": False, "msg": f"Args JSON sai: {e}"}, ensure_ascii=False))
        return

    house = (args.get("house") or "").lower()
    try:
        if house == "okvip":
            result = run_okvip(args)
        elif house == "kjc":
            result = run_kjc(args)
        else:
            result = {"ok": False, "msg": f"Nhà '{house}' không hỗ trợ (chỉ okvip|kjc)"}
    except ImportError as e:
        result = {
            "ok": False,
            "msg": (
                f"Import lỗi: {e}. Hãy chắc chắn:\n"
                f"1) REG_API_SERVER tồn tại tại {REG_API_SERVER_DIR}\n"
                f"2) Đã cài deps: pip install curl_cffi rsa pycryptodome requests"
            ),
        }
    except Exception as e:
        result = {
            "ok": False,
            "msg": f"Runtime error: {e}",
            "trace": traceback.format_exc(),
        }

    # In dòng JSON kết quả (parent đọc dòng cuối)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    # Fix Windows console encoding (giống bot.py)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
