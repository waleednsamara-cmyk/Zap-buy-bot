import json
import random
import requests
import time
import os
from threading import Thread

from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "data.json"
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
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

def poll_dexscreener(application):
    while True:
        data = load_data()
        for uid, user in data.items():
            for token in user.get("tokens", []):
                ca = token["address"]
                name = token["name"]
                try:
                    r = requests.get(DEX_API.format(ca), timeout=15)
                    if r.status_code == 200:
                        result = r.json()
                        pairs = result.get("pairs", [])
                        for pair in pairs:
                            txns = pair.get("transactions", [])
                            for txn in txns:
                                if txn.get("type") == "buy":
                                    tx_hash = txn.get("hash", "")
                                    last_buys = user.setdefault("last_buys", {})
                                    if last_buys.get(ca) == tx_hash:
                                        continue
                                    last_buys[ca] = tx_hash
                                    if user["gifs"]:
                                        gif_id = random.choice(user["gifs"])
                                        application.bot.send_animation(
                                            chat_id=uid,
                                            animation=gif_id,
                                            caption=f"🚀 Buy detected for {name}!\nTx: {tx_hash}"
                                        )
                                    else:
                                        application.bot.send_message(
                                            chat_id=uid,
                                            text=f"🚀 Buy detected for {name}!\nTx: {tx_hash}"
                                        )
                                    save_data(data)
                                    break
                except Exception as e:
                    print(f"Error polling DexScreener for {ca}: {e}")
        time.sleep(POLL_INTERVAL)

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addtoken", addtoken))
    application.add_handler(CommandHandler("removetoken", removetoken))
    application.add_handler(CommandHandler("addgif", addgif))
    application.add_handler(CommandHandler("removegif", removegif))
    application.add_handler(CommandHandler("listtokens", listtokens))
    application.add_handler(CommandHandler("listgifs", listgifs))
    application.add_handler(MessageHandler(filters.ANIMATION, gif_handler))

    poll_thread = Thread(target=poll_dexscreener, args=(application,), daemon=True)
    poll_thread.start()

    print("Bot started.")
    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())