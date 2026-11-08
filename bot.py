import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Cấu hình thông tin Bot & Admin
TOKEN = "8466851320:AAHMSKVb3n_IX2nn57BSaoC-ypkEzsKxqsI"
ADMIN_ID = 8588555065
MIN_WITHDRAW = 5000
REF_BONUS = 50

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Định nghĩa các trạng thái FSM (Nhập lệnh rút tiền, Admin cộng/trừ tiền, v.v.)
class WithdrawState(StatesGroup):
    waiting_for_info = State()

class AdminState(StatesGroup):
    waiting_for_balance = State()
    waiting_for_add_channel = State()
    waiting_for_del_channel = State()
    waiting_for_ban = State()

# Khởi tạo Database SQLite
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            referred_by INTEGER,
            balance INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            ref_id INTEGER,
            active INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_username TEXT PRIMARY KEY
        )
    """)
    # Thêm mặc định 2 kênh ban đầu nếu chưa có
    cursor.execute("INSERT OR IGNORE INTO channels (channel_username) VALUES ('@QanhMod')")
    cursor.execute("INSERT OR IGNORE INTO channels (channel_username) VALUES ('@ZenCheatsChat')")
    conn.commit()
    conn.close()

init_db()

def get_channels():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT channel_username FROM channels")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_user(user_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, referred_by, balance, is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def register_user(user_id, username, full_name, referred_by=None):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name, referred_by, balance, is_banned) VALUES (?, ?, ?, ?, 0, 0)",
        (user_id, username or "Không có", full_name or "Không có", referred_by)
    )
    # Cập nhật username/full_name nếu thay đổi
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (username or "Không có", full_name or "Không có", user_id))
    conn.commit()
    conn.close()

def update_balance(user_id, amount):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

async def check_membership(user_id: int) -> bool:
    channels = get_channels()
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logging.error(f"Lỗi kiểm tra thành viên {user_id} tại {ch}: {e}")
            return False
    return True

async def verify_and_update_refs(user_id: int):
    """Kiểm tra bạn bè được giới thiệu có còn ở trong kênh không. Nếu thoát -> Trừ tiền."""
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, ref_id FROM referrals WHERE referrer_id = ? AND active = 1", (user_id,))
    refs = cursor.fetchall()
    
    deducted = 0
    for record_id, ref_id in refs:
        still_member = await check_membership(ref_id)
        if not still_member:
            cursor.execute("UPDATE referrals SET active = 0 WHERE id = ?", (record_id,))
            deducted += REF_BONUS
    
    if deducted > 0:
        cursor.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (deducted, user_id))
        conn.commit()
        try:
            await bot.send_message(
                user_id, 
                f"⚠️ **Thông báo phạt:** Có bạn bè do bạn giới thiệu đã rời khỏi nhóm/kênh yêu cầu. Hệ thống đã trừ `{deducted}đ` khỏi số dư của bạn.",
                parse_mode="Markdown"
            )
        except:
            pass
            
    conn.commit()
    conn.close()

def get_main_keyboard(is_admin=False):
    buttons = [
        [InlineKeyboardButton(text="👤 Tài khoản & Số dư", callback_data="account"),
         InlineKeyboardButton(text="🔗 Link giới thiệu", callback_data="ref_link")],
        [InlineKeyboardButton(text="💸 Rút tiền", callback_data="withdraw"),
         InlineKeyboardButton(text="📢 Hướng dẫn & Quy định", callback_data="help")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="⚡ Bảng điều khiển Admin", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_join_keyboard():
    channels = get_channels()
    keyboard_buttons = []
    for ch in channels:
        clean_ch = ch.replace("@", "")
        keyboard_buttons.append([InlineKeyboardButton(text=f"📢 Vào Kênh {ch}", url=f"https://t.me/{clean_ch}")])
    keyboard_buttons.append([InlineKeyboardButton(text="✅ Tôi đã tham gia", callback_data="check_join")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    args = message.text.split()
    
    user = get_user(user_id)
    
    # Kiểm tra bị Ban
    if user and user[5] == 1:
        await message.answer("❌ Tài khoản của bạn đã bị khóa (Ban) khỏi hệ thống bot.")
        return

    # Nếu user đã /start trước đó rồi
    if user is not None:
        is_joined = await check_membership(user_id)
        if not is_joined:
            await message.answer(
                "❌ Bạn đã rời khỏi nhóm/kênh yêu cầu!\nVui lòng tham gia đầy đủ các kênh dưới đây để tiếp tục sử dụng bot:",
                reply_markup=get_join_keyboard()
            )
            return
        await message.answer("👋 Chào mừng bạn quay lại với Bot Kiếm Tiền!", reply_markup=get_main_keyboard(user_id == ADMIN_ID))
        return

    # User mới /start lần đầu (Chỉ tính ref nếu chưa từng tồn tại trong DB)
    ref_by = None
    if len(args) > 1 and args[1].isdigit():
        potential_ref = int(args[1])
        if potential_ref != user_id and get_user(potential_ref):
            ref_by = potential_ref

    register_user(user_id, username, full_name, referred_by=ref_by)

    is_joined = await check_membership(user_id)
    if not is_joined:
        await message.answer(
            "👋 Chào mừng bạn đến với Bot Kiếm Tiền Ref!\n\n"
            "⚠️ **Yêu cầu bắt buộc:** Bạn phải tham gia đầy đủ các nhóm/kênh sau để bắt đầu sử dụng bot:",
            reply_markup=get_join_keyboard(),
            parse_mode="Markdown"
        )
        return

    # Nếu đã tham gia đủ từ trước và có ref hợp lệ
    if ref_by:
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM referrals WHERE ref_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO referrals (referrer_id, ref_id, active) VALUES (?, ?, 1)", (ref_by, user_id))
            conn.commit()
            update_balance(ref_by, REF_BONUS)
            
            # Lấy thông tin user mời và user được mời để bắn thông báo
            referrer_info = get_user(ref_by)
            referrer_username = f"@{referrer_info[1]}" if referrer_info and referrer_info[1] != "Không có" else f"ID: {ref_by}"
            invited_username = f"@{username}" if username else f"ID: {user_id}"
            
            try:
                await bot.send_message(
                    ref_by,
                    f"😜 MEM VỀ MEM VỀ !!\n"
                    f"USER MỜI : {referrer_username}\n"
                    f"Id: {ref_by}\n"
                    f"===========================\n"
                    f"USER ĐƯỢC MỜI: {invited_username}\n"
                    f"Id:{user_id}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Lỗi gửi thông báo ref cho {ref_by}: {e}")
        conn.close()

    await message.answer(
        "✅ Xác nhận thành công! Chào mừng bạn đến với hệ thống kiếm tiền qua giới thiệu.",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )

@dp.callback_query(F.data == "check_join")
async def process_check_join(callback: CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username
    is_joined = await check_membership(user_id)
    
    if not is_joined:
        await callback.answer("❌ Bạn vẫn chưa tham gia đủ các nhóm/kênh yêu cầu!", show_alert=True)
        return
    
    # Cộng điểm cho người giới thiệu nếu có và chưa từng cộng
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        ref_by = row[0]
        cursor.execute("SELECT id FROM referrals WHERE ref_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO referrals (referrer_id, ref_id, active) VALUES (?, ?, 1)", (ref_by, user_id))
            conn.commit()
            update_balance(ref_by, REF_BONUS)
            
            referrer_info = get_user(ref_by)
            referrer_username = f"@{referrer_info[1]}" if referrer_info and referrer_info[1] != "Không có" else f"ID: {ref_by}"
            invited_username = f"@{username}" if username else f"ID: {user_id}"
            
            try:
                await bot.send_message(
                    ref_by,
                    f"😜 MEM VỀ MEM VỀ !!\n"
                    f"USER MỜI : {referrer_username}\n"
                    f"Id: {ref_by}\n"
                    f"===========================\n"
                    f"USER ĐƯỢC MỜI: {invited_username}\n"
                    f"Id:{user_id}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Lỗi gửi thông báo ref cho {ref_by}: {e}")
    conn.close()

    await callback.message.edit_text(
        "✅ Xác nhận thành công! Chào mừng bạn đến với hệ thống.",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )
    await callback.answer("Thành công!")

@dp.callback_query(F.data == "account")
async def callback_account(callback: CallbackQuery):
    user_id = callback.from_user.id
    await verify_and_update_refs(user_id)
    
    user = get_user(user_id)
    balance = user[4] if user else 0
    
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND active = 1", (user_id,))
    active_refs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND active = 0", (user_id,))
    inactive_refs = cursor.fetchone()[0]
    conn.close()
    
    text = (
        f"👤 **THÔNG TIN TÀI KHOẢN**\n\n"
        f"🆔 ID của bạn: `{user_id}`\n"
        f"💰 Số dư hiện tại: **{balance}đ**\n"
        f"👥 Số ref hợp lệ (đang hoạt động): **{active_refs}**\n"
        f"⚠️ Số ref đã rời nhóm (đã trừ tiền): **{inactive_refs}**\n\n"
        f"📌 *Quy định: 1 ref = {REF_BONUS}đ. Bạn bè rời nhóm sẽ bị trừ lại tiền.*"
    )
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id == ADMIN_ID))
    await callback.answer()

@dp.callback_query(F.data == "ref_link")
async def callback_ref_link(callback: CallbackQuery):
    user_id = callback.from_user.id
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = (
        f"🔗 **LINK GIỚI THIỆU CỦA BẠN**\n\n"
        f"Hãy chia sẻ đường dẫn bên dưới cho bạn bè để nhận ngay **{REF_BONUS}đ**/ref:\n\n"
        f"`{link}`\n\n"
        f"💡 *Mỗi bạn bè tham gia qua link và hoàn thành xác minh nhóm sẽ cộng ngay vào số dư của bạn.*"
    )
    
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id == ADMIN_ID))
    await callback.answer()

@dp.callback_query(F.data == "withdraw")
async def callback_withdraw(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await verify_and_update_refs(user_id)
    
    user = get_user(user_id)
    balance = user[4] if user else 0
    
    if balance < MIN_WITHDRAW:
        text = (
            f"💸 **RÚT TIỀN**\n\n"
            f"❌ Số dư hiện tại: **{balance}đ**\n"
            f"📌 Số dư tối thiểu để rút là **{MIN_WITHDRAW}đ**.\n\n"
            f"Hãy giới thiệu thêm bạn bè để đủ điều kiện rút tiền nhé!"
        )
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id == ADMIN_ID))
    else:
        text = (
            f"💸 **RÚT TIỀN NGAY TRÊN BOT**\n\n"
            f"✅ Số dư hiện tại: **{balance}đ**\n"
            f"📌 Vui lòng nhập thông tin rút tiền theo đúng định dạng:\n\n"
            f"`stk • ngân hàng`\n"
            f"*Ví dụ: 0123456789 • MBBank*\n\n"
            f"Gửi tin nhắn nội dung trên vào đây để hoàn tất đơn rút:"
        )
        await state.set_state(WithdrawState.waiting_for_info)
        await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@dp.message(WithdrawState.waiting_for_info)
async def process_withdraw_submission(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    text_input = message.text.strip()
    
    if "•" not in text_input:
        await message.answer("❌ Định dạng không hợp lệ! Vui lòng nhập đúng định dạng: `stk • ngân hàng`\nVí dụ: `0123456789 • MBBank`", parse_mode="Markdown")
        return
        
    user = get_user(user_id)
    balance = user[4] if user else 0
    
    if balance < MIN_WITHDRAW:
        await message.answer("❌ Số dư của bạn không đủ điều kiện rút nữa.")
        await state.clear()
        return
        
    # Trừ tiền ngay lập tức
    update_balance(user_id, -balance)
    await state.clear()
    
    # Thông báo cho user
    await message.answer(
        f"✅ **Đơn rút tiền đã được gửi thành công!**\n"
        f"💵 Số tiền: **{balance}đ**\n"
        f"🏦 Thông tin: `{text_input}`\n\n"
        f"Admin sẽ xử lý và chuyển khoản cho bạn trong thời gian sớm nhất.",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID),
        parse_mode="Markdown"
    )
    
    # Báo cáo đơn rút về cho Admin
    admin_notify = (
        f"🚨 **CÓ ĐƠN RÚT TIỀN MỚI!**\n\n"
        f"👤 User: @{username if username else 'Không có'}\n"
        f"🆔 ID: `{user_id}`\n"
        f"💰 Số tiền rút: **{balance}đ**\n"
        f"🏦 STK • Ngân hàng: `{text_input}`"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_notify, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Không thể gửi thông báo rút tiền cho Admin: {e}")

@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    channels_str = "\n   • ".join(get_channels())
    text = (
        f"ℹ️ **HƯỚNG DẪN & QUY ĐỊNH**\n\n"
        f"1️⃣ Mỗi tài khoản Telegram chỉ được `/start` ghi nhận 1 lần duy nhất.\n"
        f"2️⃣ Người được giới thiệu phải tham gia đủ các kênh/nhóm:\n   • {channels_str}\n"
        f"3️⃣ Thưởng **{REF_BONUS}đ** cho mỗi lượt ref hợp lệ.\n"
        f"4️⃣ **Phạt:** Bạn bè rời nhóm sẽ bị trừ ngay **{REF_BONUS}đ** khỏi tài khoản người giới thiệu.\n"
        f"5️⃣ Min rút: **{MIN_WITHDRAW}đ**."
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(callback.from_user.id == ADMIN_ID))
    await callback.answer()

# ================= ADMIN PANEL & COMMANDS =================

@dp.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Bạn không có quyền truy cập!", show_alert=True)
        return
        
    channels = get_channels()
    ch_text = "\n".join([f"• {c}" for c in channels])
    
    text = (
        f"⚡ **BẢNG ĐIỀU KHIỂN ADMIN**\n\n"
        f"📢 **Các kênh xác minh hiện tại:**\n{ch_text}\n\n"
        f"📌 **Danh sách lệnh quản lý (Gửi trực tiếp):**\n"
        f"➕ `/addch @kenh` - Thêm kênh xác minh\n"
        f"➖ `/delch @kenh` - Xóa kênh xác minh\n"
        f"💰 `/addbal [id] [số_tiền]` - Cộng số dư user\n"
        f"📉 `/subbal [id] [số_tiền]` - Trừ số dư user\n"
        f"🚫 `/ban [id]` - Khóa user\n"
        f"✅ `/unban [id]` - Mở khóa user\n"
        f"👥 `/refs [id]` - Xem danh sách ref của user"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Thêm kênh", callback_data="admin_add_ch"),
         InlineKeyboardButton(text="➖ Xóa kênh", callback_data="admin_del_ch")],
        [InlineKeyboardButton(text="🔙 Quay lại Menu", callback_data="back_home")]
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "back_home")
async def callback_back_home(callback: CallbackQuery):
    await callback.message.edit_text(
        "👋 Chào mừng bạn đến với hệ thống kiếm tiền qua giới thiệu.",
        reply_markup=get_main_keyboard(callback.from_user.id == ADMIN_ID)
    )
    await callback.answer()

# Thêm/Xóa kênh qua Callback hoặc Lệnh
@dp.callback_query(F.data == "admin_add_ch")
async def admin_add_ch_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.waiting_for_add_channel)
    await callback.message.answer("📥 Gửi username kênh bạn muốn thêm (Ví dụ: `@KenhMoi`):")
    await callback.answer()

@dp.message(AdminState.waiting_for_add_channel)
async def process_add_channel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    ch = message.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
        
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO channels (channel_username) VALUES (?)", (ch,))
        conn.commit()
        await message.answer(f"✅ Đã thêm kênh `{ch}` thành công!")
    except:
        await message.answer(f"⚠️ Kênh `{ch}` đã tồn tại trong hệ thống từ trước.")
    conn.close()
    await state.clear()

@dp.callback_query(F.data == "admin_del_ch")
async def admin_del_ch_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    channels = get_channels()
    ch_list = "\n".join([f"• {c}" for c in channels])
    await state.set_state(AdminState.waiting_for_del_channel)
    await callback.message.answer(f"🗑 Gửi username kênh bạn muốn xóa:\n\n{ch_list}")
    await callback.answer()

@dp.message(AdminState.waiting_for_del_channel)
async def process_del_channel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    ch = message.text.strip()
    if not ch.startswith("@"):
        ch = "@" + ch
        
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE channel_username = ?", (ch,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Đã xóa kênh `{ch}` khỏi hệ thống xác minh.")
    await state.clear()

# Lệnh quản lý trực tiếp qua chat cho Admin
@dp.message(Command("addch"))
async def cmd_addch(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Sử dụng: `/addch @username`", parse_mode="Markdown")
        return
    ch = args[1]
    if not ch.startswith("@"): ch = "@" + ch
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO channels (channel_username) VALUES (?)", (ch,))
        conn.commit()
        await message.answer(f"✅ Đã thêm kênh `{ch}`")
    except:
        await message.answer(f"⚠️ Kênh đã tồn tại.")
    conn.close()

@dp.message(Command("delch"))
async def cmd_delch(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Sử dụng: `/delch @username`", parse_mode="Markdown")
        return
    ch = args[1]
    if not ch.startswith("@"): ch = "@" + ch
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE channel_username = ?", (ch,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Đã xóa kênh `{ch}`")

@dp.message(Command("addbal"))
async def cmd_addbal(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await message.answer("Sử dụng: `/addbal [id_user] [số_tiền]`", parse_mode="Markdown")
        return
    uid, amount = int(args[1]), int(args[2])
    update_balance(uid, amount)
    await message.answer(f"✅ Đã cộng `{amount}đ` vào tài khoản ID: `{uid}`", parse_mode="Markdown")

@dp.message(Command("subbal"))
async def cmd_subbal(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
        await message.answer("Sử dụng: `/subbal [id_user] [số_tiền]`", parse_mode="Markdown")
        return
    uid, amount = int(args[1]), int(args[2])
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = MAX(0, balance - ?) WHERE user_id = ?", (amount, uid))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Đã trừ `{amount}đ` khỏi tài khoản ID: `{uid}`", parse_mode="Markdown")

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Sử dụng: `/ban [id_user]`", parse_mode="Markdown")
        return
    uid = int(args[1])
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    await message.answer(f"🚫 Đã khóa tài khoản ID: `{uid}` thành công.", parse_mode="Markdown")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Sử dụng: `/unban [id_user]`", parse_mode="Markdown")
        return
    uid = int(args[1])
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Đã mở khóa tài khoản ID: `{uid}`.", parse_mode="Markdown")

@dp.message(Command("refs"))
async def cmd_refs(message: Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Sử dụng: `/refs [id_user]`", parse_mode="Markdown")
        return
    uid = int(args[1])
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT ref_id, active FROM referrals WHERE referrer_id = ?", (uid,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer(f"ℹ️ User ID `{uid}` chưa giới thiệu được ai.")
        return
        
    text = f"👥 **DANH SÁCH REF CỦA ID: {uid}**\n\n"
    for r_id, active in rows:
        status = "✅ Hoạt động" else "❌ Đã rời nhóm"
        text += f"• ID: `{r_id}` - Trạng thái: {status}\n"
        
    await message.answer(text, parse_mode="Markdown")

async def main():
    print("Bot with Advanced Admin & Withdraw features is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
