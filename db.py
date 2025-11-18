import aiosqlite
from datetime import datetime
import asyncio

DB_PATH = "bot.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                bonus_unlocked INTEGER DEFAULT 0,
                referred_by INTEGER,
                lang TEXT DEFAULT 'en'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                valid INTEGER DEFAULT 0,
                valid_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                status TEXT,
                created_at TEXT,
                bank TEXT,
                account_number TEXT,
                account_name TEXT
            )
        """)
        await db.commit()

async def add_user(user_id, referred_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)",
            (user_id, referred_by)
        )
        if referred_by:
            await db.execute(
                "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                (referred_by, user_id)
            )
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_referral(referrer_id, referred_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
            (referrer_id, referred_id)
        )
        await db.commit()

async def set_referral_valid(referred_id):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().strftime('%Y-%m-%d')
        await db.execute(
            "UPDATE referrals SET valid = 1, valid_at = ? WHERE referred_id = ?",
            (now, referred_id)
        )
        await db.commit()

async def count_valid_referrals(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND valid = 1", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def count_today_valid_referrals(user_id):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND valid = 1 AND valid_at = ?",
            (user_id, today)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def unlock_bonus(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + 30, bonus_unlocked = 1 WHERE user_id = ? AND bonus_unlocked = 0",
            (user_id,)
        )
        await db.commit()

async def add_ongoing_referral_bonus(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + 2.5 WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()

async def get_top_referrers(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT referrer_id, COUNT(*) as cnt FROM referrals WHERE valid = 1 GROUP BY referrer_id ORDER BY cnt DESC LIMIT ?",
            (limit,)
        ) as cursor:
            return await cursor.fetchall()

async def add_withdrawal(user_id, amount, status="Pending", bank=None, account_number=None, account_name=None):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
        await db.execute(
            "INSERT INTO withdrawals (user_id, amount, status, created_at, bank, account_number, account_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, amount, status, now, bank, account_number, account_name)
        )
        await db.commit()

async def get_recent_withdrawals(user_id, limit=5):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT amount, status, created_at FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

async def set_user_lang(user_id, lang):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
        await db.commit()

async def get_user_lang(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 'am'

async def get_pending_withdrawals(limit=5):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, amount, status, created_at, bank, account_number, account_name FROM withdrawals WHERE status = 'Pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        ) as cursor:
            return await cursor.fetchall()

async def update_withdrawal_status(withdrawal_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE withdrawals SET status = ? WHERE id = ?",
            (status, withdrawal_id)
        )
        await db.commit()

async def update_user_balance(user_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

if __name__ == "__main__":
    asyncio.run(init_db()) 