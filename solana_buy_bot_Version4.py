import json
import random
import requests
import time
import os
import asyncio

from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BIRDEYE_API_KEY = os.environ.get("BIRDEYE_API_KEY")  # optional, improves accuracy
DATA_FILE = "data.json"
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
BIRDEYE_TX_API = (
    "https://public-api.birdeye.so/defi/txs/token?address={address}&offset=0&limit=1"
)
POLL_INTERVAL = 60  # seconds

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome!\n"
        "Commands:\n"
        "/addtoken <contract_address> <token_name> - Add Solana token\n"
        "/addgif - Upload a GIF (max 5)\n"
        "/listtokens - List your tokens\n"
        "/listgifs - List your GIFs\n"
        "/removetoken <contract_address> - Remove token\n"
        "/removegif <index> - Remove GIF by index\n"
    )

async def addtoken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /addtoken <contract_address> <token_name>")
        return
    ca = args[0]
    name = " ".join(args[1:]) if len(args) > 1 else ca
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.setdefault(uid, {"tokens": [], "gifs": [], "last_buys": {}})
    for t in user["tokens"]:
        if t["address"] == ca:
            await update.message.reply_text("Token already added.")
            return
    user["tokens"].append({"chain": "SOLANA", "address": ca, "name": name})
    save_data(data)
    await update.message.reply_text(f"Token '{name}' added.")

async def removetoken(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /removetoken <contract_address>")
        return
    ca = args[0]
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.get(uid, None)
    if not user:
        await update.message.reply_text("No tokens found.")
        return
    user["tokens"] = [t for t in user["tokens"] if t["address"] != ca]
    save_data(data)
    await update.message.reply_text("Token removed.")

async def addgif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send a GIF file (max 5 per user).")

async def gif_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.setdefault(uid, {"tokens": [], "gifs": [], "last_buys": {}})
    if len(user["gifs"]) >= 5:
        await update.message.reply_text("Maximum of 5 GIFs reached. Use /removegif to delete one.")
        return
    if not update.message.animation:
        await update.message.reply_text("No GIF detected. Please send a valid animated GIF.")
        return
    gif_id = update.message.animation.file_id
    user["gifs"].append(gif_id)
    save_data(data)
    await update.message.reply_text("GIF added!")

async def removegif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /removegif <index>")
        return
    try:
        idx = int(args[0])
    except Exception:
        await update.message.reply_text("Index must be an integer (see /listgifs for indices).")
        return
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.get(uid, None)
    if not user or idx < 0 or idx >= len(user["gifs"]):
        await update.message.reply_text("Invalid index.")
        return
    del user["gifs"][idx]
    save_data(data)
    await update.message.reply_text("GIF removed.")

async def listtokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.get(uid, {"tokens": []})
    tokens = user["tokens"]
    if not tokens:
        await update.message.reply_text("No tokens added.")
        return
    msg = "Your tokens:\n"
    for t in tokens:
        msg += f"{t['name']} ({t['address']})\n"
    await update.message.reply_text(msg)

async def listgifs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    user = data.get(uid, {"gifs": []})
    gifs = user["gifs"]
    if not gifs:
        await update.message.reply_text("No GIFs added.")
        return
    for i, gif_id in enumerate(gifs):
        await update.message.reply_text(f"GIF #{i}:")
        await context.bot.send_animation(chat_id=update.effective_chat.id, animation=gif_id)

async def _fetch_json(url: str, headers: dict | None = None) -> dict | None:
    try:
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
    except Exception as e:
        print(f"HTTP call failed for {url}: {e}")
        return None
    try:
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Non-200 response {response.status_code} for {url}")
            return None
    except Exception as e:
        print(f"JSON parse failed for {url}: {e}")
        return None


async def _fetch_dexscreener_buys_count(ca: str) -> int:
    url = DEX_API.format(ca)
    data = await _fetch_json(url)
    if not data:
        return 0
    pairs = data.get("pairs", [])
    # Aggregate 5-minute buy counts across pairs as a rough buy activity proxy
    buys_m5_total = 0
    for pair in pairs:
        txns = pair.get("txns", {}) or {}
        m5 = txns.get("m5", {}) or {}
        buys_m5_total += int(m5.get("buys", 0) or 0)
    return buys_m5_total


async def _fetch_birdeye_latest_buy(ca: str) -> tuple[str | None, float | None]:
    if not BIRDEYE_API_KEY:
        return None, None
    url = BIRDEYE_TX_API.format(address=ca)
    headers = {
        "x-chain": "solana",
        "X-API-KEY": BIRDEYE_API_KEY,
        "accept": "application/json",
    }
    data = await _fetch_json(url, headers=headers)
    if not data:
        return None, None
    # Attempt to parse a recent BUY-like transaction; fields vary, so be defensive
    try:
        items = (
            data.get("data", {}).get("items")
            or data.get("data", {}).get("transactions")
            or data.get("items")
            or []
        )
        if not items:
            return None, None
        tx = items[0]
        side = (tx.get("side") or tx.get("type") or tx.get("txType") or "").lower()
        if "buy" in side:
            tx_hash = tx.get("txHash") or tx.get("hash") or tx.get("signature")
            ts = (
                tx.get("blockTime")
                or tx.get("timestamp")
                or tx.get("time")
                or time.time()
            )
            return tx_hash, float(ts)
        return None, None
    except Exception:
        return None, None


async def poll_job(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    for uid, user in data.items():
        for token in user.get("tokens", []):
            ca = token["address"]
            name = token["name"]
            try:
                # Prefer precise detection when Birdeye API key is available
                tx_hash, _ = await _fetch_birdeye_latest_buy(ca)
                if tx_hash:
                    last_buys = user.setdefault("last_buys", {})
                    if last_buys.get(ca) != tx_hash:
                        last_buys[ca] = tx_hash
                        if user["gifs"]:
                            gif_id = random.choice(user["gifs"])
                            await context.bot.send_animation(
                                chat_id=int(uid),
                                animation=gif_id,
                                caption=f"🚀 Buy detected for {name}!\nTx: {tx_hash}",
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=int(uid),
                                text=f"🚀 Buy detected for {name}!\nTx: {tx_hash}",
                            )
                        save_data(data)
                        continue  # proceed to next token

                # Fallback: use DexScreener aggregated 5m buys as a proxy
                buys_m5_total = await _fetch_dexscreener_buys_count(ca)
                last_buys_counts = user.setdefault("last_buys_counts", {})
                previous_count = int(last_buys_counts.get(ca, 0))
                if buys_m5_total > previous_count:
                    delta = buys_m5_total - previous_count
                    last_buys_counts[ca] = buys_m5_total
                    msg = (
                        f"🚀 Buy activity for {name}: +{delta} buys in last 5m\n"
                        f"(Total m5 buys now {buys_m5_total})"
                    )
                    if user.get("gifs"):
                        gif_id = random.choice(user["gifs"])
                        await context.bot.send_animation(
                            chat_id=int(uid),
                            animation=gif_id,
                            caption=msg,
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=int(uid),
                            text=msg,
                        )
                    save_data(data)
            except Exception as e:
                print(f"Error polling for {ca}: {e}")

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addtoken", addtoken))
    application.add_handler(CommandHandler("removetoken", removetoken))
    application.add_handler(CommandHandler("addgif", addgif))
    application.add_handler(CommandHandler("removegif", removegif))
    application.add_handler(CommandHandler("listtokens", listtokens))
    application.add_handler(CommandHandler("listgifs", listgifs))
    application.add_handler(MessageHandler(filters.ANIMATION, gif_handler))

    # Use PTB job queue to schedule async polling instead of a blocking thread
    application.job_queue.run_repeating(poll_job, interval=POLL_INTERVAL, first=5)

    print("Bot started.")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())