import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from config import BOT_TOKEN, CHANNEL_USERNAME, CHANNEL_LINK, GROUP_USERNAME, GROUP_LINK, BOT_NAME, SIGNUP_LINK, LOGIN_LINK, SPORT_LINK, CASINO_LINK, SUPPORT_LINK, REFERRAL_IMAGE_PATH
from db import init_db, add_user, get_user, add_referral, count_valid_referrals, set_referral_valid, unlock_bonus, add_ongoing_referral_bonus, set_user_lang, get_user_lang, get_pending_withdrawals, update_withdrawal_status, update_user_balance, add_withdrawal
import telegram
from telegram.constants import ChatMemberStatus
import requests
import aiosqlite
from lang import LANG
import warnings
import openpyxl
import asyncio

warnings.filterwarnings('ignore')

# Suppress all other logs except our prints
for logger_name in ['httpx', 'apscheduler', 'telegram.ext', '__main__']:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

print("Bot run successfully")

# Conversation states
WITHDRAW_AMOUNT, WITHDRAW_PHONE, WITHDRAW_ID, WITHDRAW_CONFIRM = range(4)

ADMIN_USER_IDS = [368455563]


def _on_startup(app):
    asyncio.create_task(app.init_db_job(None))
    asyncio.create_task(app.set_bot_commands(None))


class TelegramBot:
    def __init__(self):
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Add command handlers
        self.application.add_handler(
            CommandHandler("start", self.start_command))
        self.application.add_handler(
            CommandHandler("referral", self.referral_command))
        self.application.add_handler(
            CommandHandler("balance", self.balance_command))
        self.application.add_handler(
            CommandHandler("myearnings", self.cmd_my_earnings))
        self.application.add_handler(
            CommandHandler("myreferrals", self.cmd_my_referrals))
        self.application.add_handler(
            CommandHandler("withdraw", self.cmd_withdraw))
        self.application.add_handler(
            CommandHandler("referrallink", self.cmd_referral_link))
        self.application.add_handler(
            CommandHandler("leaderboard", self.cmd_leaderboard))
        self.application.add_handler(
            CommandHandler("history", self.cmd_history))
        self.application.add_handler(
            CommandHandler("settings", self.cmd_settings))
        self.application.add_handler(
            CommandHandler("language", self.cmd_language))
        self.application.add_handler(
            CommandHandler("admin_withdrawals",
                           self.admin_withdrawals_command))
        self.application.add_handler(
            CommandHandler("admin_approved_withdrawals",
                           self.admin_approved_withdrawals_command))
        self.application.add_handler(
            CommandHandler("admin", self.admin_panel_command))
        self.application.add_handler(
            CommandHandler("admin_export_users",
                           self.admin_export_users_command))
        self.application.add_handler(
            CommandHandler("help", self.help_command))
        # Invalid message handler disabled to avoid interfering with conversations
        # self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_invalid_message), group=-1)
        # Handler order: admin_withdrawal_action first, then withdraw_conv, then button_callback
        self.application.add_handler(CallbackQueryHandler(self.admin_withdrawal_action, pattern=r"^admin_withdraw_(approve|reject)_\d+$"))
        withdraw_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.withdraw_start, pattern="^withdraw$")],
            states={
                WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.withdraw_amount_handler)],
                WITHDRAW_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.withdraw_phone_handler)],
                WITHDRAW_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.withdraw_id_handler)],
                WITHDRAW_CONFIRM: [CallbackQueryHandler(self.withdraw_confirm_handler, pattern="^withdraw_(confirm|cancel)$")],
            },
            fallbacks=[CommandHandler("cancel", self.withdraw_cancel)],
            per_user=True,
            per_chat=True,
        )
        self.application.add_handler(withdraw_conv)
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
    
    async def startup(self):
        await self.init_db_job(None)
        await self.set_bot_commands()
        
    async def init_db_job(self, context):
        await init_db()
        
    async def set_bot_commands(self, update=None, context=None):
        """Set bot commands in the menu"""
        commands = [
            BotCommand("start", "🔄 Start - Restart"),
            BotCommand("referral", "🔗 Referral")
        ]
        await self.application.bot.set_my_commands(commands)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        username = user.username if user.username else 'No username'
        args = context.args
        referred_by = int(args[0]) if args and args[0].isdigit() and int(args[0]) != user.id else None
        await add_user(user.id, referred_by)
        # Immediately unlock bonus and credit 30 ETB on signup
        user_row = await get_user(user.id)
        if user_row and not user_row[3]:
            await unlock_bonus(user.id)
        if not await self.is_user_member(context, user.id, CHANNEL_USERNAME):
            await self.show_join_channel_message(update)
        elif not await self.is_user_member(context, user.id, GROUP_USERNAME):
            await self.show_join_group_message(update)
        else:
            await self.show_referral_info(update, user.id)
    
    async def show_join_channel_message(self, update: Update):
        if not update.effective_user:
            return
        user_lang = await get_user_lang(update.effective_user.id)
        message_text = f"{LANG[user_lang]['welcome'].format(name=update.effective_user.first_name)}\n\n{LANG[user_lang]['join_channel']}"
        keyboard = [
            [
                InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK),
                InlineKeyboardButton("✅ Joined Channel", callback_data="check_channel_join")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text(message_text, reply_markup=reply_markup)
    
    async def show_join_group_message(self, update: Update, query=None):
        if not update.effective_user:
            return
        user_lang = await get_user_lang(update.effective_user.id)
        message_text = f"{LANG[user_lang]['join_group']}\n\n{LANG[user_lang]['join_group_desc']}"
        keyboard = [
            [
                InlineKeyboardButton("👥 Join Group", url=GROUP_LINK),
                InlineKeyboardButton("✅ Joined", callback_data="check_group_join")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await query.edit_message_text(message_text, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(message_text, reply_markup=reply_markup)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query or not hasattr(query, "data") or query.data is None or not hasattr(query, "from_user") or query.from_user is None:
            return
        await query.answer()
        user_id = query.from_user.id
        if query.data == "check_channel_join":
            if await self.is_user_member(context, user_id, CHANNEL_USERNAME):
                if not await self.is_user_member(context, user_id, GROUP_USERNAME):
                    await self.show_join_group_message(update, query)
                else:
                    # Mark referral as valid if user was referred and now joined both
                    user_row = await get_user(user_id)
                    if user_row and user_row[4]:
                        await set_referral_valid(user_id)
                        referrer_id = user_row[4]
                        valid_referrals = await count_valid_referrals(referrer_id)
                        referrer = await get_user(referrer_id)
                        if valid_referrals == 10 and referrer and not referrer[3]:
                            await unlock_bonus(referrer_id)
                        elif valid_referrals > 10:
                            await add_ongoing_referral_bonus(referrer_id)
                    await self.show_referral_info(update, user_id, query)
            else: 
                await self.show_not_joined_message(query, CHANNEL_USERNAME, CHANNEL_LINK, "channel", "check_channel_join")
        elif query.data == "check_group_join":
            if await self.is_user_member(context, user_id, GROUP_USERNAME):
                # Mark referral as valid if user was referred and now joined both
                user_row = await get_user(user_id)
                if user_row and user_row[4]:
                    await set_referral_valid(user_id)
                    referrer_id = user_row[4]
                    valid_referrals = await count_valid_referrals(referrer_id)
                    referrer = await get_user(referrer_id)
                    if valid_referrals == 5 and referrer and not referrer[3]:
                        await unlock_bonus(referrer_id)
                    elif valid_referrals > 5:
                        await add_ongoing_referral_bonus(referrer_id)
                await self.show_referral_info(update, user_id, query)
            else:
                await self.show_not_joined_message(query, GROUP_USERNAME, GROUP_LINK, "group", "check_group_join")
        elif query.data == "back_home":
            await self.show_referral_info(update, user_id, query)
        elif query.data == "my_earnings":
            await self.show_earnings(query, user_id)
        elif query.data == "my_referrals":
            await self.show_my_referrals(query, user_id)
        elif query.data == "withdraw":
            # Start withdraw conversation
            await self.withdraw_start(update, context)
        elif query.data == "leaderboard":
            await self.show_leaderboard(query)
        elif query.data == "history":
            await self.show_history(query, user_id)
        elif query.data == "settings":
            await self.show_subscription_status(query, user_id, context)
        elif query.data == "rules":
            await self.show_rules(query)
        elif query.data == "faq":
            await self.show_faq(query)
        elif query.data == "copy_referral_link":
            await self.copy_referral_link(query, user_id)
        elif query.data == "invite_now":
            await self.invite_now(query, user_id)
        elif query.data == "referral_link":
            await self.show_referral_link(query, user_id)
        elif query.data == "language":
            await self.show_language_panel(query)

        elif query.data == "set_lang_en":
            await set_user_lang(user_id, 'en')
            keyboard = [[InlineKeyboardButton(LANG['en']['back_home'], callback_data="back_home")]]
            await query.edit_message_text(LANG['en']['select_language'] + "\nLanguage set to English.", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        elif query.data == "set_lang_am":
            await set_user_lang(user_id, 'am')
            keyboard = [[InlineKeyboardButton(LANG['am']['back_home'], callback_data="back_home")]]
            await query.edit_message_text(LANG['am']['select_language'] + "\nቋንቋ ወደ አማርኛ ተቀይሯል።", reply_markup=InlineKeyboardMarkup(keyboard))
            return
    
    async def is_user_member(self, context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_username: str) -> bool:
        try:
            chat_id = chat_username if chat_username.startswith('-100') else f"@{chat_username.lstrip('@')}"
            print(f"[DEBUG] Checking membership: chat_id={chat_id}, user_id={user_id}")
            chat_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            print(f"[DEBUG] chat_member status: {chat_member.status}")
            return chat_member.status in ['member', 'administrator', 'creator', 'owner', 'restricted']
        except Exception as e:
            print(f"[ERROR] is_user_member error: {e}")
            return False
    
    async def show_not_joined_message(self, query, username, link, type_label, callback_data):
        user_lang = await get_user_lang(query.from_user.id)
        button_label = "📢 Join Channel" if type_label == "channel" else "👥 Join Group"
        joined_label = "✅ Joined Channel" if type_label == "channel" else "✅ Joined"
        keyboard = [
            [
                InlineKeyboardButton(button_label, url=link),
                InlineKeyboardButton(joined_label, callback_data=callback_data)
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = (
            f"❌ You have not joined the {type_label} yet!\n\n"
            f"Please join our {type_label} first."
        )
        if query:
            await query.edit_message_text(text=message_text, reply_markup=reply_markup)
    
    async def show_referral_info(self, update: Update, user_id: int, query=None):
        user_lang = await get_user_lang(user_id)
        valid_referrals = await count_valid_referrals(user_id)
        user = await get_user(user_id)
        # Only unlock balance if 10 valid referrals and bonus is unlocked
        if valid_referrals >= 10 and user and user[3]:
            balance = user[1]
            unlocked = "Yes"
            balance_display = f"{balance} ETB"
        else:
            balance = 0
            unlocked = "No"
            balance_display = "Locked"
        message = (
            f"{LANG[user_lang]['welcome_new'].format(count=valid_referrals, balance='Locked' if not (user and user[3] and valid_referrals >= 10) else f'{user[1]} ETB', can_withdraw='Yes' if (user and user[3] and valid_referrals >= 10) else 'No')}"
        )
        keyboard = [
            [
                InlineKeyboardButton("🎮 Signup", url=SIGNUP_LINK),
                InlineKeyboardButton("🔐 Login", url=LOGIN_LINK)
            ],
            [
                InlineKeyboardButton("⚽ Sport", url=SPORT_LINK),
                InlineKeyboardButton("🎰 Casino", url=CASINO_LINK)
            ],
            [
                InlineKeyboardButton(LANG[user_lang]['my_earnings'], callback_data="my_earnings"),
                InlineKeyboardButton(LANG[user_lang]['my_referrals'], callback_data="my_referrals")
            ],
            [
                InlineKeyboardButton(LANG[user_lang]['withdraw'], callback_data="withdraw"),
                InlineKeyboardButton(LANG[user_lang]['referral_link'], callback_data="referral_link")
            ],
            [
                InlineKeyboardButton(LANG[user_lang]['leaderboard'], callback_data="leaderboard"),
                InlineKeyboardButton(LANG[user_lang]['history'], callback_data="history")
            ],
            [
                InlineKeyboardButton(LANG[user_lang]['settings'], callback_data="settings"),
                InlineKeyboardButton(LANG[user_lang]['language'], callback_data="language")
            ],
            [
                InlineKeyboardButton(LANG[user_lang]['rules'], callback_data="rules"),
                InlineKeyboardButton("💬 Support", url=SUPPORT_LINK)
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            try:
                await query.edit_message_text(message, reply_markup=reply_markup)
            except telegram.error.BadRequest as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    raise
        else:
            if update.message:
                await update.message.reply_text(message, reply_markup=reply_markup)
    
    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return
        user_lang = await get_user_lang(user_id)
        valid_referrals = await count_valid_referrals(user_id)
        user = await get_user(user_id)
        balance = user[1] if user else 0
        unlocked = "Yes" if user and user[3] else "No"
        if update.message:
            await update.message.reply_text(
                f"{LANG[user_lang]['balance']}: {balance} ETB\n"
                f"{LANG[user_lang]['valid_referrals']}: {valid_referrals}\n"
                f"{LANG[user_lang]['bonus_unlocked']}: {unlocked}"
            )
    
    async def show_earnings(self, target, user_id):
        user_lang = await get_user_lang(user_id)
        user = await get_user(user_id)
        valid_referrals = await count_valid_referrals(user_id)
        # Only unlock earnings if 5 valid referrals and bonus is unlocked
        if valid_referrals >= 10 and user and user[3]:
            total_earned = 30 + max(0, valid_referrals - 10)
            locked_bonus = "No"
            available_balance = user[1]
            can_withdraw = available_balance >= 30
        else:
            total_earned = 0
            locked_bonus = "Yes"
            available_balance = "Locked"
            can_withdraw = False
        message = (
            f"{LANG[user_lang]['total_earned']}: {total_earned} ETB\n"
            f"{LANG[user_lang]['locked_bonus']}: {locked_bonus}\n"
            f"{LANG[user_lang]['available_balance']}: {available_balance if valid_referrals >= 10 and user and user[3] else 'Locked'} ETB"
        )
        keyboard = []
        if can_withdraw:
            keyboard.append([InlineKeyboardButton(LANG[user_lang]['withdraw'], callback_data="withdraw")])
        keyboard.append([InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(message, reply_markup=reply_markup)
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(message, reply_markup=reply_markup)
        else:
            await target.reply_text(message, reply_markup=reply_markup)
    
    async def show_my_referrals(self, target, user_id):
        from db import count_today_valid_referrals
        user_lang = await get_user_lang(user_id)
        valid_referrals = await count_valid_referrals(user_id)
        today_referrals = await count_today_valid_referrals(user_id)
        next_target = max(0, 10 - valid_referrals)
        next_target_text = f"{next_target} {LANG[user_lang]['more_to_unlock']}" if next_target > 0 else LANG[user_lang]['unlocked']
        message = (
            f"{LANG[user_lang]['total_referred_users']}: {valid_referrals}\n"
            f"{LANG[user_lang]['referred_today']}: {today_referrals}\n"
            f"{LANG[user_lang]['next_target']}: {next_target_text}"
        )
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            try:
                await target.edit_message_text(message, reply_markup=reply_markup)
            except telegram.error.BadRequest as e:
                if "Message is not modified" in str(e):
                    pass
                else:
                    raise
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(message, reply_markup=reply_markup)
        else:
            await target.reply_text(message, reply_markup=reply_markup)

    async def copy_referral_link(self, query, user_id):
        user_lang = await get_user_lang(user_id)
        referral_link = f"https://t.me/{BOT_NAME.lstrip('@')}?start={user_id}"
        await query.answer("Link copied! (Copy manually)", show_alert=True)
        
        # Image path for referral link
        with open(REFERRAL_IMAGE_PATH, 'rb') as photo:
            await query.edit_message_media(
                media=telegram.InputMediaPhoto(media=photo, caption=f"{LANG[user_lang]['referral_link']}:\n{referral_link}\n\n(You can copy it manually.)"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Home", callback_data="back_home")]])
            )

    async def invite_now(self, query, user_id):
        user_lang = await get_user_lang(user_id)
        referral_link = f"https://t.me/{BOT_NAME.lstrip('@')}?start={user_id}"
        await query.edit_message_text(f"{LANG[user_lang]['invite_now']}\n{referral_link}",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Home", callback_data="back_home")]]),
                                      disable_web_page_preview=True)

    async def show_leaderboard(self, target):
        from db import get_top_referrers
        user_lang = await get_user_lang(target.effective_user.id if hasattr(target, 'effective_user') else target.from_user.id)
        top_referrers = await get_top_referrers()
        if not top_referrers:
            message = LANG[user_lang]['no_referrals']
        else:
            lines = [LANG[user_lang]['top_referrers']]
            for idx, (user_id, count) in enumerate(top_referrers, 1):
                user = await get_user(user_id)
                username = user[0] if user and user[0] else f"{user_id}"
                try:
                    chat = await target.bot.get_chat(user_id)
                    display_name = f"@{chat.username}" if chat.username else chat.first_name or str(user_id)
                except Exception:
                    display_name = username
                lines.append(f"{idx}. {display_name} - {count} {LANG[user_lang]['referrals']}")
            message = "\n".join(lines)
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(message, reply_markup=reply_markup)
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(message, reply_markup=reply_markup)
        else:
            await target.reply_text(message, reply_markup=reply_markup)

    async def show_history(self, target, user_id):
        from db import get_recent_withdrawals
        user_lang = await get_user_lang(user_id)
        withdrawals = await get_recent_withdrawals(user_id)
        if not withdrawals:
            message = LANG[user_lang]['no_withdrawal_history']
        else:
            lines = [LANG[user_lang]['last_withdrawals']]
            for amount, status, created_at in withdrawals:
                lines.append(f"{created_at} | {amount} ETB | {status}")
            message = "\n".join(lines)
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(message, reply_markup=reply_markup)
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(message, reply_markup=reply_markup)
        else:
            await target.reply_text(message, reply_markup=reply_markup)

    async def show_settings(self, target):
        user_lang = await get_user_lang(target.effective_user.id if hasattr(target, 'effective_user') else target.from_user.id)
        keyboard = [
            [InlineKeyboardButton(LANG[user_lang]['view_subscription_status'], callback_data="view_subscription_status")],
            [InlineKeyboardButton(LANG[user_lang]['rules'], callback_data="rules")],
            [InlineKeyboardButton(LANG[user_lang]['faq'], callback_data="faq")],
            [InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(LANG[user_lang]['settings'] + ":", reply_markup=reply_markup)
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(LANG[user_lang]['settings'] + ":", reply_markup=reply_markup)
        else:
            await target.reply_text(LANG[user_lang]['settings'] + ":", reply_markup=reply_markup)

    async def show_subscription_status(self, query, user_id, context):
        user_lang = await get_user_lang(user_id)
        channel_status = group_status = "❌ Not joined"
        try:
            chat_member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
            print(f"[DEBUG][show_subscription_status] channel chat_member.status: {chat_member.status}")
            if chat_member.status in ['member', 'administrator', 'creator', 'owner', 'restricted']:
                channel_status = "✅ Joined"
        except Exception as e:
            print(f"[ERROR][show_subscription_status] channel: {e}")
        try:
            chat_member = await context.bot.get_chat_member(chat_id=GROUP_USERNAME, user_id=user_id)
            print(f"[DEBUG][show_subscription_status] group chat_member.status: {chat_member.status}")
            if chat_member.status in ['member', 'administrator', 'creator', 'owner', 'restricted']:
                group_status = "✅ Joined"
        except Exception as e:
            print(f"[ERROR][show_subscription_status] group: {e}")
        print(f"[DEBUG][show_subscription_status] channel_status: {channel_status}, group_status: {group_status}")
        msg = f"{LANG[user_lang]['channel']}: {channel_status}\n{LANG[user_lang]['group']}: {group_status}"
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_rules(self, query):
        user_lang = await get_user_lang(query.from_user.id)
        msg = LANG[user_lang]['rules_text']
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        try:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise

    async def show_faq(self, query):
        user_lang = await get_user_lang(query.from_user.id)
        msg = LANG[user_lang]['faq_text']
        keyboard = [[InlineKeyboardButton(LANG[user_lang]['back_home'], callback_data="back_home")]]
        try:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise

    async def show_referral_link(self, target, user_id):
        user_lang = await get_user_lang(user_id)
        referral_link = f"https://t.me/{BOT_NAME.lstrip('@')}?start={user_id}"
        if user_lang == 'am':
            message = (
                "💰ከቤቶ ሆነው ገንዘብ ይስሩ! 💵 \n\n"
                "⭕️ይህን ቦት ተቀላቅለው ለሰው በማስተላለፍ ብቻ በቀን ከ1000 ብር በላይ ይስሩ ፣ በተጨማሪም በየቀኑ ከምንሸልማቸው 5 Iphone 16 Pro Max ስልኮች 1 ይሸለሙ።\n\n"
                "🔗የሪፈራል ሊንክ፡\n"
                f"{referral_link}"
            )
        else:
            message = (
                "💰Earn Money from Home! 💵\n\n"
                "⭕️Join this bot and by simply sharing it with others, earn over 1000 Birr daily; plus, every day you have a chance to win 1 of 5 iPhone 16 Pro Max!\n\n"
                "🔗Referral Link:\n"
                f"{referral_link}"
            )
        
        # Send image with referral link
        if hasattr(target, 'edit_message_media'):
            # For query callbacks, edit with media
            with open(REFERRAL_IMAGE_PATH, 'rb') as photo:
                await target.edit_message_media(
                    media=telegram.InputMediaPhoto(media=photo, caption=message),
                    reply_markup=None
                )
        elif hasattr(target, 'message') and target.message:
            # For message replies, send new photo
            with open(REFERRAL_IMAGE_PATH, 'rb') as photo:
                await target.message.reply_photo(photo=photo, caption=message, parse_mode="HTML")
        else:
            # For direct replies
            with open(REFERRAL_IMAGE_PATH, 'rb') as photo:
                await target.reply_photo(photo=photo, caption=message, parse_mode="HTML")

    async def show_language_panel(self, target):
        user_lang = await get_user_lang(target.effective_user.id if hasattr(target, 'effective_user') else target.from_user.id)
        keyboard = [
            [
                InlineKeyboardButton(LANG['en']['english'], callback_data="set_lang_en"),
                InlineKeyboardButton(LANG['en']['amharic'], callback_data="set_lang_am")
            ],
            [
                InlineKeyboardButton(LANG['en']['back_home'], callback_data="back_home")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(target, 'edit_message_text'):
            await target.edit_message_text(LANG['en']['select_language'], reply_markup=reply_markup)
        elif hasattr(target, 'message') and target.message:
            await target.message.reply_text(LANG['en']['select_language'], reply_markup=reply_markup)
        else:
            await target.reply_text(LANG['en']['select_language'], reply_markup=reply_markup)

    async def withdraw_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Support both CallbackQuery and Message
        user_id = None
        if hasattr(update, 'callback_query') and update.callback_query:
            query = update.callback_query
            user_id = query.from_user.id if query and query.from_user else None
        elif update.effective_user:
            user_id = update.effective_user.id
        if user_id is not None:
            user = await get_user(user_id)
            valid_referrals = await count_valid_referrals(user_id)
            user_lang = await get_user_lang(user_id)
            # Prevent withdraw if not eligible
            if not (user and user[3] and valid_referrals >= 10 and user[1] >= 30):
                msg = LANG[user_lang]['withdraw_eligibility_error']
                # Clear any withdraw-related context
                if context.user_data is not None:
                    context.user_data.pop('withdraw_amount', None)
                    context.user_data.pop('withdraw_phone', None)
                    context.user_data.pop('withdraw_id', None)
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.answer()
                    await update.callback_query.edit_message_text(msg)
                elif update.message:
                    await update.message.reply_text(msg)
                from telegram.ext import ConversationHandler
                return ConversationHandler.END
        if hasattr(update, 'callback_query') and update.callback_query:
            query = update.callback_query
            await query.answer()
            user_lang = await get_user_lang(query.from_user.id)
            msg = LANG[user_lang]['withdraw_how_much'] + "\n\n" + LANG[user_lang]['withdraw_minmax']
            if query:
                await query.edit_message_text(msg, parse_mode="HTML")
            return WITHDRAW_AMOUNT
        else:
            user_lang = await get_user_lang(update.effective_user.id) if update.effective_user else None
            msg = LANG[user_lang]['withdraw_how_much'] + "\n\n" + LANG[user_lang]['withdraw_minmax']
            if update.message:
                await update.message.reply_text(msg, parse_mode="HTML")
            return WITHDRAW_AMOUNT

    async def withdraw_amount_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return WITHDRAW_AMOUNT
        user_lang = await get_user_lang(user_id)
        user = await get_user(user_id)
        balance = user[1] if user else 0
        text = update.message.text.strip() if update.message and update.message.text else None
        if not text:
            return WITHDRAW_AMOUNT
        try:
            amount = int(text)
        except Exception:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['withdraw_invalid'])
            return WITHDRAW_AMOUNT
        if amount < 30:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['withdraw_min'])
            return WITHDRAW_AMOUNT
        if amount > balance:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['withdraw_max'].format(balance=balance))
            return WITHDRAW_AMOUNT
        if context.user_data is not None:
            context.user_data['withdraw_amount'] = amount
        if update.message:
            await update.message.reply_text(
                LANG[user_lang]['withdraw_enter_phone'],
                reply_markup=ReplyKeyboardRemove()
            )
        return WITHDRAW_PHONE

    async def withdraw_phone_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return WITHDRAW_PHONE
        user_lang = await get_user_lang(user_id)

        phone = update.message.text.strip() if update.message and update.message.text else None
        if not phone:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['withdraw_enter_phone'])
            return WITHDRAW_PHONE
        if context.user_data is not None:
            context.user_data['withdraw_phone'] = phone
        if update.message:
            await update.message.reply_text(LANG[user_lang]['withdraw_enter_id'], reply_markup=ReplyKeyboardRemove())
        return WITHDRAW_ID

    async def withdraw_id_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return WITHDRAW_ID
        user_lang = await get_user_lang(user_id)

        nana_id = update.message.text.strip() if update.message and update.message.text else None
        phone = context.user_data.get('withdraw_phone', '') if context.user_data else ''
        amount = context.user_data.get('withdraw_amount', 0) if context.user_data else 0
        if context.user_data is not None:
            context.user_data['withdraw_id'] = nana_id
        # Calculate fee and net amount
        fee = round(amount * 0.033, 2)
        net = round(amount - fee, 2)

        msg = LANG[user_lang]['withdraw_confirm'].format(
            phone=phone,
            nana_id=nana_id,
            amount=amount,
            fee=fee,
            net=net
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data="withdraw_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="withdraw_cancel")
            ]
        ]
        if update.message:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return WITHDRAW_CONFIRM

    async def withdraw_confirm_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle withdraw confirm/cancel callbacks"""
        query = update.callback_query
        if not query:
            return ConversationHandler.END
        
        await query.answer()
        user_id = query.from_user.id if query.from_user else None
        if not user_id:
            return ConversationHandler.END
        
        user_lang = await get_user_lang(user_id)
        
        if query.data == "withdraw_confirm":
            # Get withdrawal data from context
            amount = context.user_data.get('withdraw_amount', 0) if context.user_data else 0
            phone = context.user_data.get('withdraw_phone', '') if context.user_data else ''
            nana_id = context.user_data.get('withdraw_id', '') if context.user_data else ''
            
            if amount > 0:
                # Add withdrawal request to DB
                await add_withdrawal(user_id, amount, status='Pending', bank=phone, account_number=nana_id, account_name=None)
                
                # Deduct user balance immediately
                await update_user_balance(user_id, amount)
                
                # Clear context data
                if context.user_data:
                    context.user_data.pop('withdraw_amount', None)
                    context.user_data.pop('withdraw_phone', None)
                    context.user_data.pop('withdraw_id', None)
                
                await query.edit_message_text(LANG[user_lang]['withdraw_success'])
            else:
                await query.edit_message_text("❌ Error: Invalid withdrawal data")
            
            return ConversationHandler.END
            
        elif query.data == "withdraw_cancel":
            # Clear context data
            if context.user_data:
                context.user_data.pop('withdraw_amount', None)
                context.user_data.pop('withdraw_phone', None)
                context.user_data.pop('withdraw_id', None)
            
            await query.edit_message_text(LANG[user_lang]['withdraw_cancelled'])
            return ConversationHandler.END
        
        return ConversationHandler.END

    async def withdraw_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return ConversationHandler.END
        user_lang = await get_user_lang(user_id)
        
        if update.message:
            await update.message.reply_text(LANG[user_lang]['withdraw_cancelled'])
        if context.user_data is not None:
            context.user_data.pop('withdraw_amount', None)
            context.user_data.pop('withdraw_phone', None)
            context.user_data.pop('withdraw_id', None)
        return ConversationHandler.END

    async def admin_withdrawals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return
        user_lang = await get_user_lang(user_id)
        if user_id not in ADMIN_USER_IDS:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['admin_only'])
            return
        pending = await get_pending_withdrawals()
        if not pending:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['admin_no_withdrawals'])
            return
        for w in pending:
            wid, uid, amount, status, created_at, bank, account_number, account_name = w
            msg = (
                f"{LANG[user_lang]['admin_withdrawals_title']}\n\n"
                f"User ID: {uid}\n"
                f"Amount: {amount} ETB\n"
                f"Requested: {created_at}\n"
                f"Phone: {bank or '-'}\n"
                f"NanaBet ID: {account_number or '-'}"
            )
            keyboard = [
                [
                    InlineKeyboardButton(LANG[user_lang]['admin_withdraw_approve'], callback_data=f"admin_withdraw_approve_{wid}"),
                    InlineKeyboardButton(LANG[user_lang]['admin_withdraw_reject'], callback_data=f"admin_withdraw_reject_{wid}")
                ]
            ]
            if update.message:
                await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    async def admin_withdrawal_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
            user_id = query.from_user.id if query and query.from_user else None
            if user_id is None:
                return
            user_lang = await get_user_lang(user_id)
            if user_id not in ADMIN_USER_IDS:
                if query:
                    await query.answer()
                    await query.edit_message_text(LANG[user_lang]['admin_only'])
                return
            data = query.data if query and hasattr(query, 'data') else None
            if not data:
                return
            parts = data.split('_') if data else []
            if len(parts) < 4:
                return
            action, wid = parts[2], int(parts[3])
            # Get withdrawal info
            async with aiosqlite.connect('bot.db') as db:
                async with db.execute("SELECT user_id, amount FROM withdrawals WHERE id = ?", (wid,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        if query:
                            await query.edit_message_text("Withdrawal not found.")
                        return
                    target_uid, amount = row
            target_lang = await get_user_lang(target_uid)
            if action == 'approve':
                await update_withdrawal_status(wid, 'Completed')
                await context.bot.send_message(target_uid, LANG[target_lang]['admin_withdraw_user_approved'])
                if query:
                    await query.edit_message_text(LANG[user_lang]['admin_withdraw_approved'])
            elif action == 'reject':
                await update_withdrawal_status(wid, 'Rejected')
                # Refund the deducted amount
                await update_user_balance(target_uid, -amount)
                await context.bot.send_message(target_uid, LANG[target_lang]['admin_withdraw_user_rejected'])
                if query:
                    await query.edit_message_text(LANG[user_lang]['admin_withdraw_rejected'])
        except Exception as e:
            print("ADMIN WITHDRAW ERROR:", e)
            if 'query' in locals() and query:
                await query.edit_message_text("An error occurred. Please contact support.")

    async def admin_approved_withdrawals_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return
        user_lang = await get_user_lang(user_id)
        if user_id not in ADMIN_USER_IDS:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['admin_only'])
            return
        # Get last 5 approved withdrawals
        async with aiosqlite.connect('bot.db') as db:
            async with db.execute("SELECT user_id, amount, created_at, bank, account_number, account_name FROM withdrawals WHERE status = 'Completed' ORDER BY id DESC LIMIT 5") as cursor:
                rows = await cursor.fetchall()
        if not rows:
            if update.message:
                await update.message.reply_text(LANG[user_lang].get('admin_no_approved_withdrawals', 'No approved withdrawals found.'))
            return
        for row in rows:
            uid, amount, created_at, bank, account_number, account_name = row
            msg = (
                f"<b>{LANG[user_lang]['admin_withdrawals_title']}</b>\n\n"
                f"User ID: {uid}\n"
                f"Amount: {amount} ETB\n"
                f"Requested: {created_at}\n"
                f"Phone: {bank or '-'}\n"
                f"NanaBet ID: {account_number or '-'}"
            )
            if update.message:
                await update.message.reply_text(msg, parse_mode="HTML")

    async def admin_panel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return
        user_lang = await get_user_lang(user_id)
        if user_id not in ADMIN_USER_IDS:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['admin_only'])
            return
        msg = (
            f"<b>{LANG[user_lang]['admin_panel_title'] if 'admin_panel_title' in LANG[user_lang] else '🛡 Admin Panel'}</b>\n\n"
            f"/admin_withdrawals - {LANG[user_lang].get('admin_panel_withdrawals', 'View pending withdrawals')}\n"
            f"/admin_approved_withdrawals - {LANG[user_lang].get('admin_panel_approved', 'View approved withdrawals')}\n"
            f"/admin_export_users - {LANG[user_lang].get('admin_panel_export', 'Export all users to Excel')}\n"
        )
        if update.message:
            await update.message.reply_text(msg, parse_mode="HTML")

    async def admin_export_users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id is None:
            return
        user_lang = await get_user_lang(user_id)
        if user_id not in ADMIN_USER_IDS:
            if update.message:
                await update.message.reply_text(LANG[user_lang]['admin_only'])
            return
        # Fetch all users
        async with aiosqlite.connect('bot.db') as db:
            users = await db.execute("SELECT user_id, balance, referrals, lang FROM users")
            users = await users.fetchall()
        # Prepare Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        if ws:
            ws.title = "Users"
            ws.append(["User Name", "User ID", "Total Referral", "Total Balance", "Withdraw History"])
            for u in users:
                uid, balance, referrals, lang = u
                # Try to get username
                try:
                    chat = await context.bot.get_chat(uid)
                    username = chat.username or chat.full_name or str(uid)
                except:
                    username = str(uid)
                # Get withdraw history
                async with aiosqlite.connect('bot.db') as db:
                    withdrawals = await db.execute("SELECT amount, status, created_at, bank, account_number, account_name FROM withdrawals WHERE user_id = ? ORDER BY id ASC", (uid,))
                    withdrawals = await withdrawals.fetchall()
                if withdrawals:
                    whist = []
                    for w in withdrawals:
                        amount, status, created_at, bank, account_number, account_name = w
                        whist.append(f"{created_at} | {amount} ETB | Phone: {bank or '-'} | NanaBet ID: {account_number or '-'} | {status}")
                    whist_str = "; ".join(whist)
                else:
                    whist_str = "-"
                if ws:
                    ws.append([username, uid, referrals, balance, whist_str])
        # Save to file
        file_path = "users_export.xlsx"
        wb.save(file_path)
        # Send file
        if update.message:
            with open(file_path, "rb") as f:
                await update.message.reply_document(f, filename="users_export.xlsx")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        user_lang = await get_user_lang(update.effective_user.id)
        help_text = f"""
{LANG[user_lang]['welcome'].format(name=update.effective_user.first_name)}

Available commands:
/start - Start the bot
/balance - Check your balance
/withdraw - Withdraw money
/referral_link - Get your referral link
/my_earnings - View your earnings
/my_referrals - View your referrals
/leaderboard - View top referrers
/history - View withdrawal history
/settings - Bot settings
/help - Show this help message

Admin commands:
/admin_panel - Admin panel (admin only)
/admin_withdrawals - View pending withdrawals (admin only)
/admin_approved - View approved withdrawals (admin only)
/admin_export - Export users to Excel (admin only)
        """
        await update.message.reply_text(help_text)

    async def handle_invalid_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle invalid text messages gracefully - only in private chats"""
        if not update.effective_user:
            return
        
        # Only respond to invalid commands in private chats, not in groups
        if update.message.chat.type != "private":
            return
        
        # Don't interfere with withdrawal conversation or other conversations
        if context.user_data and any(key.startswith('withdraw_') for key in context.user_data.keys()):
            return
        
        # Don't interfere if user is in any conversation state
        if hasattr(context, 'user_data') and context.user_data:
            return
        
        # Don't interfere with withdrawal conversation
        if context.user_data and any(key.startswith('withdraw_') for key in context.user_data.keys()):
            return
        
        # Don't interfere with any active conversation
        if context.user_data and len(context.user_data) > 0:
            return
        
        # Don't interfere with any active conversation
        if context.user_data and len(context.user_data) > 0:
            return
            
        user_lang = await get_user_lang(update.effective_user.id)
        await update.message.reply_text(LANG[user_lang]['invalid_command'])

    async def referral_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /referral command"""
        user = update.effective_user
        if not user:
            return
        
        user_id = user.id
        user_lang = await get_user_lang(user_id)
        referral_link = f"https://t.me/{BOT_NAME.lstrip('@')}?start={user_id}"
        
        message = (
            f"{LANG[user_lang]['promo_message']}\n"
            f"{referral_link}"
        )
        
        await update.message.reply_text(message, disable_web_page_preview=True)

    # Slash command handlers for menu commands
    async def cmd_my_earnings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id:
            await self.show_earnings(update, update.effective_user.id)

    async def cmd_my_referrals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id:
            await self.show_my_referrals(update, update.effective_user.id)

    async def cmd_withdraw(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Start withdraw process (simulate button click)
        await self.withdraw_start(update, context)

    async def cmd_referral_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id:
            await self.show_referral_link(update, update.effective_user.id)

    async def cmd_leaderboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_leaderboard(update)

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id:
            await self.show_history(update, update.effective_user.id)

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_settings(update)

    async def cmd_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_language_panel(update)

    def run(self):
        # Only run polling; do not call asyncio.run(self.startup()) to avoid event loop errors
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    bot = TelegramBot()
    bot.run() 