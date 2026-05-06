import logging
import yfinance as yf
from google import genai
import requests
import pytz
from datetime import datetime, time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

import os

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
T212_API_KEY = os.environ.get('T212_API_KEY')
T212_API_SECRET = os.environ.get('T212_API_SECRET')

import base64
credentials = f"{T212_API_KEY}:{T212_API_SECRET}"
encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
t212_headers = {"Authorization": f"Basic {encoded}"}
BASE_URL = "https://live.trading212.com/api/v0"

client = genai.Client(api_key=GEMINI_API_KEY)

uk_tz = pytz.timezone('Europe/London')

def safe_change(current, open_price):
    if open_price and open_price > 0:
        return ((current - open_price) / open_price) * 100
    return 0.0

def get_asset_data(ticker, name, is_gold=False):
    try:
        stock = yf.Ticker(ticker)
        history = stock.history(period="3mo")
        info = stock.info
        if history.empty:
            return None
        history['MA20'] = history['Close'].rolling(window=20).mean()
        current_price = history['Close'].iloc[-1]
        ma20 = history['MA20'].iloc[-1]
        week52_high = history['High'].max()
        week52_low = history['Low'].min()
        open_price = info.get('open', 0)
        todays_change = safe_change(current_price, open_price)
        position = ((current_price - week52_low) / (week52_high - week52_low)) * 100
        currency = "$" if is_gold else "£"
        above_below = "above" if current_price > ma20 else "below"
        return {
            "name": name,
            "ticker": ticker,
            "price": current_price,
            "change": todays_change,
            "position": position,
            "ma20": ma20,
            "week52_high": week52_high,
            "week52_low": week52_low,
            "currency": currency,
            "above_below": above_below,
            "is_gold": is_gold
        }
    except Exception:
        return None

def get_all_assets():
    assets = [
        ("BT-A.L", "📡 BT Group", False),
        ("VOD.L", "📱 Vodafone", False),
        ("LLOY.L", "🏦 Lloyds", False),
        ("LGEN.L", "💰 Legal & General", False),
        ("NG.L", "⚡ National Grid", False),
        ("GC=F", "🥇 Gold", True),
    ]
    results = []
    for ticker, name, is_gold in assets:
        data = get_asset_data(ticker, name, is_gold)
        if data:
            results.append(data)
    return results

def get_account_cash():
    try:
        response = requests.get(f"{BASE_URL}/equity/account/cash", headers=t212_headers)
        if response.status_code == 200:
            cash = response.json()
            return cash.get('free', 0)
        return None
    except Exception:
        return None

def ask_master_agent(question, asset_data):
    context = "Current market data:\n"
    for d in asset_data:
        context += (
            f"{d['name']}: {d['currency']}{d['price']:.2f} "
            f"({d['change']:+.2f}% today) | "
            f"Price {d['above_below']} 20day avg | "
            f"{d['position']:.0f}% of 52wk range\n"
        )

    prompt = (
        "You are a Master Trading Agent — a personal AI trading assistant. "
        "You support a retail investor who prefers buying dips and selling when in profit. "
        "The investor trades UK stocks and Gold on Trading 212 ISA. "
        "Always give clear, simple, plain English answers. Never use jargon. "
        "Always remind the user this is not financial advice at the end. "
        f"\n\nCurrent market data:\n{context}\n\n"
        f"User question: {question}\n\n"
        "Answer helpfully and concisely in under 150 words."
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt
        )
        return response.text
    except Exception:
        return "Sorry I couldn't process that right now — please try again in a moment."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 Hello! I'm your Master Trading Agent!\n\n"
        "I can help you with:\n"
        "📊 /briefing — Full market briefing\n"
        "💰 /account — Your Trading 212 balance\n"
        "📈 /snapshot — Quick market snapshot\n"
        "🥇 /gold — Gold analysis\n"
        "📡 /bt — BT Group analysis\n"
        "🏦 /lloyds — Lloyds analysis\n\n"
        "Or just ask me anything about the market! 💬\n\n"
        "⚠️ Not financial advice."
    )
    await update.message.reply_text(welcome)

async def briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 Generating your morning briefing — one moment...")
    asset_data = get_all_assets()

    if not asset_data:
        await update.message.reply_text("❌ Could not fetch market data right now — try again shortly.")
        return

    snapshot = "📊 <b>Market Snapshot:</b>\n"
    for d in asset_data:
        change_icon = "📈" if d['change'] > 0 else "📉"
        snapshot += f"{change_icon} {d['name']}: {d['currency']}{d['price']:.2f} ({d['change']:+.2f}%)\n"

    analysis = ask_master_agent(
        "Give me a full morning briefing covering best opportunity, what to avoid, "
        "overall market mood and one key thing to watch today.",
        asset_data
    )

    message = f"{snapshot}\n🧠 <b>Master Analysis:</b>\n{analysis}"
    await update.message.reply_text(message, parse_mode='HTML')

async def account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cash = get_account_cash()
    if cash is not None:
        await update.message.reply_text(
            f"💼 <b>Your Trading 212 Account</b>\n\n"
            f"Free Cash Available: £{cash:,.2f}\n\n"
            f"✅ Account connected and active",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text("❌ Could not connect to Trading 212 right now.")

async def snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Fetching market snapshot...")
    asset_data = get_all_assets()

    if not asset_data:
        await update.message.reply_text("❌ Could not fetch market data right now.")
        return

    msg = "📊 <b>Market Snapshot</b>\n\n"
    for d in asset_data:
        change_icon = "📈" if d['change'] > 0 else "📉"
        if d['position'] >= 80:
            pos_icon = "🔴"
        elif d['position'] <= 30:
            pos_icon = "🟢"
        else:
            pos_icon = "🟡"
        msg += (
            f"{change_icon} <b>{d['name']}</b>\n"
            f"   Price: {d['currency']}{d['price']:.2f} ({d['change']:+.2f}%)\n"
            f"   {pos_icon} {d['position']:.0f}% of 52wk range\n"
            f"   📊 {d['above_below'].capitalize()} 20day average\n\n"
        )
    await update.message.reply_text(msg, parse_mode='HTML')

async def bt_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📡 Analysing BT Group...")
    data = get_asset_data("BT-A.L", "BT Group")
    if data:
        analysis = ask_master_agent(
            "Give me a specific analysis of BT Group — should I buy, sell or wait? "
            "Include the key level to watch and main risk.",
            [data]
        )
        msg = (
            f"📡 <b>BT Group Analysis</b>\n\n"
            f"Price: £{data['price']:.2f} ({data['change']:+.2f}%)\n"
            f"20 Day Average: £{data['ma20']:.2f}\n"
            f"Price is {data['above_below']} average\n"
            f"{data['position']:.0f}% of 52wk range\n\n"
            f"🧠 {analysis}"
        )
        await update.message.reply_text(msg, parse_mode='HTML')

async def lloyds_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏦 Analysing Lloyds...")
    data = get_asset_data("LLOY.L", "Lloyds Banking Group")
    if data:
        analysis = ask_master_agent(
            "Give me a specific analysis of Lloyds Banking Group — should I buy, sell or wait? "
            "Include the key level to watch and main risk.",
            [data]
        )
        msg = (
            f"🏦 <b>Lloyds Analysis</b>\n\n"
            f"Price: £{data['price']:.2f} ({data['change']:+.2f}%)\n"
            f"20 Day Average: £{data['ma20']:.2f}\n"
            f"Price is {data['above_below']} average\n"
            f"{data['position']:.0f}% of 52wk range\n\n"
            f"🧠 {analysis}"
        )
        await update.message.reply_text(msg, parse_mode='HTML')

async def gold_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🥇 Analysing Gold...")
    data = get_asset_data("GC=F", "Gold", is_gold=True)
    if data:
        analysis = ask_master_agent(
            "Give me a specific analysis of Gold — should I buy, sell or wait? "
            "Include the key level to watch and main risk.",
            [data]
        )
        msg = (
            f"🥇 <b>Gold Analysis</b>\n\n"
            f"Price: ${data['price']:.2f} ({data['change']:+.2f}%)\n"
            f"20 Day Average: ${data['ma20']:.2f}\n"
            f"Price is {data['above_below']} average\n"
            f"{data['position']:.0f}% of 52wk range\n\n"
            f"🧠 {analysis}"
        )
        await update.message.reply_text(msg, parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    await update.message.reply_text("🧠 Thinking...")
    asset_data = get_all_assets()
    response = ask_master_agent(user_message, asset_data)
    await update.message.reply_text(response)

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("briefing", briefing))
    app.add_handler(CommandHandler("account", account))
    app.add_handler(CommandHandler("snapshot", snapshot))
    app.add_handler(CommandHandler("bt", bt_analysis))
    app.add_handler(CommandHandler("lloyds", lloyds_analysis))
    app.add_handler(CommandHandler("gold", gold_analysis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🧠 Master Trading Agent Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
