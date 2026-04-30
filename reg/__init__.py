"""
NEWBOT — Reg module
====================
Module đăng ký nick cho 2 nhà:
  • OKVIP (5 sites): F168, C168, CM88, SC88, FLY88 — gọi REG_API_SERVER/autoreg_v2.py
  • KJC   (7 sites): F168, C168, CM88, SC88, FLY88, LLWIN, GG88
                     — gọi REG_API_SERVER/Reg Nha KJC/core_logic.py

Tách module rõ ràng để dễ tháo lắp:
  • runner.py    — CLI subprocess wrapper, gọi logic từ REG_API_SERVER trực tiếp
  • handlers.py  — Telegram menu / inline keyboard / flow nhập input

CÁCH HOẠT ĐỘNG:
Bot KHÔNG copy logic reg vào trong code — thay vào đó dùng subprocess để chạy
runner.py như 1 Python process độc lập. Mỗi job reg = 1 process riêng:
  • Tránh xung đột module giữa OKVIP và KJC (cả 2 đều có 'modules/' folder)
  • Logic core (autoreg_v2.py / core_logic.py) ở REG_API_SERVER vẫn là source of
    truth duy nhất — không duplicate code.
  • Khi user update REG_API_SERVER, NEWBOT tự động được update theo.

ĐỂ XOÁ TÍNH NĂNG NÀY:
  1. Xoá thư mục NEWBOT/reg/
  2. Xoá phần `import reg.handlers` và `reg.handlers.register(...)` trong bot.py
  3. Xoá nút "🚀 Tạo Nick" khỏi main_menu() trong bot.py
"""
