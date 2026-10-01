# Telegram Commerce Bot — Starter Template
# Copy this file. Fill in TOKEN + Tripay credentials. Run with python3.

import os, sys, sqlite3, logging, json, uuid, requests, asyncio
from contextlib import closing
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG — Fill these in ───
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("SHOP_ADMIN_ID", "0"))
DB_PATH = os.path.expanduser("~/shopbot/database.db")

TRIPAY_API_KEY = os.environ.get("TRIPAY_API_KEY", "")
TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_MERCHANT_CODE = os.environ.get("TRIPAY_MERCHANT_CODE", "")

# Import Tripay gateway
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tripay_gateway import TripayPayment
tripay = TripayPayment(TRIPAY_API_KEY, TRIPAY_PRIVATE_KEY, TRIPAY_MERCHANT_CODE)

# ─── PRODUCTS — Edit these ───
PRODUCTS = {
    "netflix_1m": {"name": "Netflix Premium 1 Bulan", "price": 35000, "cat": "Netflix"},
    "netflix_3m": {"name": "Netflix Premium 3 Bulan", "price": 90000, "cat": "Netflix"},
    "gpt_plus": {"name": "ChatGPT Plus 1 Bulan", "price": 150000, "cat": "AI"},
    "gpt_share": {"name": "ChatGPT Shared Akun", "price": 50000, "cat": "AI"},
    "spotify_1m": {"name": "Spotify Premium 1 Bulan", "price": 25000, "cat": "Music"},
}

# ─── DATABASE ───
def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT, product TEXT, price INTEGER,
        status TEXT DEFAULT 'pending',
        tripay_ref TEXT, created_at TEXT, paid_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product TEXT, credential TEXT,
        status TEXT DEFAULT 'available', used_by INTEGER
    )''')
    columns = {row[1] for row in c.execute('PRAGMA table_info(orders)')}
    if 'stock_id' not in columns:
        c.execute('ALTER TABLE orders ADD COLUMN stock_id INTEGER')
    if 'payment_amount' not in columns:
        c.execute('ALTER TABLE orders ADD COLUMN payment_amount INTEGER')
    conn.commit()
    conn.close()

# ─── KEYBOARDS ───
def main_menu():
    cats = sorted(set(p["cat"] for p in PRODUCTS.values()))
    buttons = []
    row = []
    for cat in cats:
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat.lower()}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("📋 Order Saya", callback_data="my_orders")])
    buttons.append([InlineKeyboardButton("ℹ️ Bantuan", callback_data="help")])
    return InlineKeyboardMarkup(buttons)

def category_menu(cat_key):
    cat_name = cat_key.replace("cat_", "").capitalize()
    buttons = []
    for key, p in PRODUCTS.items():
        if p["cat"].lower() == cat_key.replace("cat_", ""):
            buttons.append([InlineKeyboardButton(
                f"{p['name']} — Rp{p['price']:,}",
                callback_data=f"buy_{key}"
            )])
    buttons.append([InlineKeyboardButton("⬅️ Kembali", callback_data="home")])
    return InlineKeyboardMarkup(buttons)

# ─── HANDLERS ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 **Kedai Ochobot**\n\nPilih kategori:",
        reply_markup=main_menu(), parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "home":
        await query.edit_message_text("🛒 Pilih kategori:", reply_markup=main_menu(), parse_mode="Markdown")

    elif data == "help":
        await query.edit_message_text(
            "ℹ️ **Cara Order:**\n\n1. Pilih produk\n2. Scan QRIS\n3. Kredensial dikirim otomatis\n\n📞 Bantuan: hubungi admin",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="home")]]),
            parse_mode="Markdown"
        )

    elif data.startswith("cat_"):
        await query.edit_message_text("📋 Pilih produk:", reply_markup=category_menu(data), parse_mode="Markdown")

    elif data.startswith("buy_"):
        product_key = data.replace("buy_", "")
        p = PRODUCTS.get(product_key)
        if not p:
            return

        await query.edit_message_text("⏳ Membuat QRIS...")

        order_uuid = f"ORDER-{uuid.uuid4().hex[:8].upper()}"
        qris = await asyncio.to_thread(tripay.create_qris,
            amount=p['price'],
            customer_name=query.from_user.username or "Customer",
            order_id=order_uuid,
            expired=24
        )

        if not qris.get("success"):
            await query.edit_message_text(
                f"❌ Gagal: {qris.get('error', 'Coba lagi')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="home")]])
            )
            return

        # Save order
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO orders (user_id, username, product, price, status, tripay_ref, created_at, payment_amount) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (user_id, query.from_user.username or "unknown", p['name'], p['price'], qris['reference'], datetime.now().isoformat(), qris['amount'])
        )
        order_id = c.lastrowid
        conn.commit()
        conn.close()

        # Send QR as photo
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=qris['qr_url'],
            caption=f"📦 Order #{order_id}\n{p['name']}\nRp{p['price']:,}\n\n📱 Scan QRIS di atas"
        )

        await query.edit_message_text(
            f"QRIS dikirim 👆\nScan & bayar. Kredensial otomatis.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Cek Status", callback_data=f"check_{order_id}")],
                [InlineKeyboardButton("⬅️ Batal", callback_data="home")],
            ]),
            parse_mode="Markdown"
        )

    elif data.startswith("check_"):
        try:
            order_id = int(data.removeprefix("check_"))
        except ValueError:
            return
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT tripay_ref, product, payment_amount, status FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
        row = c.fetchone()
        conn.close()

        if not row:
            await query.answer("Order tidak ditemukan")
            return

        ref, product, price, status = row

        check = await asyncio.to_thread(tripay.check_payment, ref)
        if (check.get("success") is True and check.get("status") == "PAID"
                and check.get("reference") == ref and type(price) is int and price > 0
                and check.get("amount") == price):
            with closing(sqlite3.connect(DB_PATH)) as conn:
                conn.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=? AND status='pending'",
                             (datetime.now().isoformat(), order_id))
                conn.commit()
            delivered = await deliver_order(order_id, context.bot)
            await query.edit_message_text(
                ("✅ Pembayaran terkonfirmasi. Kredensial sudah dikirim." if delivered
                 else "Pembayaran terkonfirmasi; pengiriman tertunda. Hubungi admin."),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="home")]]),
                parse_mode="Markdown"
            )
        else:
            await query.answer("Belum ada pembayaran. Silakan scan QRIS dulu.", show_alert=True)

    elif data == "my_orders":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, product, price, status, created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        orders = c.fetchall()
        conn.close()

        if not orders:
            text = "📋 Belum ada order."
        else:
            text = "📋 *Order Terakhir:*\n\n"
            for oid, prod, price, st, created in orders:
                emoji = {"pending": "⏳", "done": "✅", "rejected": "❌"}.get(st, "🔄")
                text += f"#{oid} {emoji} {prod}\n   Rp{price:,} • {created[:10]}\n"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="home")]]),
            parse_mode="Markdown"
        )

# ─── DELIVERY ───
async def deliver_order(order_id, bot):
    """Reserve stock atomically; only mark done after an awaited Telegram send.

    A crashed 'delivering' order needs operator reconciliation. Never release a
    reserved credential on ambiguous network failure: it may already be exposed.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            'SELECT user_id, product, status, stock_id FROM orders WHERE id=?',
            (order_id,)).fetchone()
        if row and row[2] == 'done':
            conn.rollback()
            return True
        if not row or row[2] not in ('paid', 'delivery_failed'):
            conn.rollback()
            return False
        buyer_id, product, _, reserved_id = row
        if reserved_id is not None:
            stock = conn.execute(
                "SELECT id, credential FROM stock WHERE id=? AND status='reserved' AND used_by=?",
                (reserved_id, buyer_id)).fetchone()
        else:
            stock = conn.execute(
                "SELECT id, credential FROM stock WHERE product=? AND status='available' ORDER BY id LIMIT 1",
                (product,)).fetchone()
        if stock:
            stock_id, credential = stock
            conn.execute("UPDATE stock SET status='reserved', used_by=? WHERE id=?", (buyer_id, stock_id))
            conn.execute("UPDATE orders SET status='delivering', stock_id=? WHERE id=?", (stock_id, order_id))
        conn.commit()
    finally:
        conn.close()
    if not stock:
        await bot.send_message(ADMIN_ID, f"Stok tidak tersedia: {product} (Order #{order_id})")
        return False
    try:
        await bot.send_message(
            buyer_id, f"Pembayaran terkonfirmasi!\nOrder #{order_id}: {product}\n\nKredensial:\n{credential}\n\nTerima kasih!")
    except Exception:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute("UPDATE orders SET status='delivery_failed' WHERE id=? AND status='delivering'", (order_id,))
            conn.commit()
        logger.warning('Delivery failed for order #%s; reserved stock retained', order_id)
        return False
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE stock SET status='used' WHERE id=?", (stock_id,))
        conn.execute("UPDATE orders SET status='done' WHERE id=?", (order_id,))
        conn.commit()
    logger.info('Order #%s delivered', order_id)
    return True

# ─── ADMIN COMMANDS ───
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg.text:
        return
    user_id = msg.from_user.id
    if user_id != ADMIN_ID:
        return

    text = msg.text.strip()
    if text.startswith("/addstock_"):
        # /addstock_<ProductName>_<credential>
        parts = text.replace("/addstock_", "", 1).rsplit("_", 1)
        if len(parts) == 2:
            product_name, credential = parts
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO stock (product, credential) VALUES (?, ?)", (product_name, credential))
            conn.commit()
            conn.close()
            await msg.reply_text(f"✅ Stok ditambahkan: {product_name}")

    elif text.startswith("/stok"):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT product, COUNT(*) FROM stock WHERE status='available' GROUP BY product")
        rows = c.fetchall()
        conn.close()
        if rows:
            reply = "📦 *Stok:*\n\n" + "\n".join(f"• {p}: {n} pcs" for p, n in rows)
        else:
            reply = "📦 Stok kosong."
        await msg.reply_text(reply, parse_mode="Markdown")

# ─── MAIN ───
def main():
    if not all((TOKEN, ADMIN_ID, TRIPAY_API_KEY, TRIPAY_PRIVATE_KEY, TRIPAY_MERCHANT_CODE)):
        raise SystemExit('Set TELEGRAM_BOT_TOKEN, SHOP_ADMIN_ID and TRIPAY_* environment variables')
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    logger.info("🚀 Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
