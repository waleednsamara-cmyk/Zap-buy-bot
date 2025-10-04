import json
import random
import requests
import time
import os
import base64
import asyncio
from dotenv import load_dotenv

from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Solana + Jupiter imports for buy execution
from solders.keypair import Keypair
from solders.transaction import Transaction
from solana.rpc.api import Client
import base58

# Load environment variables from a local .env file if present
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = os.environ.get("DATA_FILE", "data.json")
DEX_API = os.environ.get("DEX_API", "https://api.dexscreener.com/latest/dex/tokens/{}")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # seconds

# Solana/Jupiter configuration
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
JUP_QUOTE_URL = os.environ.get("JUP_QUOTE_URL", "https://quote-api.jup.ag/v6/quote")
JUP_SWAP_URL = os.environ.get("JUP_SWAP_URL", "https://quote-api.jup.ag/v6/swap")
WSOL_MINT = "So11111111111111111111111111111111111111112"
SLIPPAGE_BPS = int(os.environ.get("SLIPPAGE_BPS", "100"))  # 1%
WALLET_SECRET_KEY = os.environ.get("WALLET_SECRET_KEY")  # base58 string or JSON [..]

sol_client = Client(RPC_URL)

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

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


# ---- Buy execution helpers ----
def _load_keypair_from_env():
    if not WALLET_SECRET_KEY:
        return None
    try:
        sk = WALLET_SECRET_KEY.strip()
        if sk.startswith("[") and sk.endswith("]"):
            arr = json.loads(sk)
            secret = bytes(arr)
            return Keypair.from_bytes(secret)
        # assume base58 string
        return Keypair.from_base58_string(sk)
    except Exception as e:
        print(f"Failed to parse WALLET_SECRET_KEY: {e}")
        return None


WALLET = _load_keypair_from_env()


def _lamports(amount_sol: float) -> int:
    return int(round(amount_sol * 1_000_000_000))


def _jup_quote(output_mint: str, amount_lamports: int, slippage_bps: int = SLIPPAGE_BPS):
    params = {
        "inputMint": WSOL_MINT,
        "outputMint": output_mint,
        "amount": str(amount_lamports),
        "slippageBps": str(slippage_bps),
        "onlyDirectRoutes": "false",
    }
    r = requests.get(JUP_QUOTE_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _jup_swap(quote_json: dict, user_pubkey: str):
    payload = {
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "asLegacyTransaction": True,
        "quoteResponse": quote_json,
    }
    r = requests.post(JUP_SWAP_URL, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def execute_buy_sync(output_mint: str, amount_sol: float) -> str:
    if WALLET is None:
        raise RuntimeError("Wallet not configured. Set WALLET_SECRET_KEY in environment.")
    quote = _jup_quote(output_mint, _lamports(amount_sol))
    swap = _jup_swap(quote, str(WALLET.public_key))
    swap_tx_b64 = swap.get("swapTransaction")
    if not swap_tx_b64:
        raise RuntimeError(f"Swap API did not return transaction: {swap}")
    tx_bytes = base64.b64decode(swap_tx_b64)
    tx = Transaction.from_bytes(tx_bytes)
    signed_tx = tx.sign([WALLET], tx.message.recent_blockhash)
    raw = bytes(signed_tx)
    resp = sol_client.send_raw_transaction(raw)
    # resp structure may vary; try common keys
    if isinstance(resp, dict) and "result" in resp:
        sig = resp["result"]
    elif hasattr(resp, "value"):
        sig = resp.value
    else:
        sig = str(resp)
    return sig


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /buy <mint_address> <amount_in_SOL>")
        return
    mint = args[0]
    try:
        amount_sol = float(args[1])
        if amount_sol <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Amount must be a positive number, e.g. 0.1")
        return

    if WALLET is None:
        await update.message.reply_text("Wallet not configured. Set WALLET_SECRET_KEY in environment and restart bot.")
        return

    await update.message.reply_text(f"Placing buy: {amount_sol} SOL -> {mint} ...")
    try:
        loop = asyncio.get_running_loop()
        signature = await loop.run_in_executor(None, execute_buy_sync, mint, amount_sol)
        await update.message.reply_text(
            f"✅ Buy submitted. Signature: {signature}\nhttps://solscan.io/tx/{signature}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Buy failed: {e}")

async def poll_dexscreener_task(context: ContextTypes.DEFAULT_TYPE):
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
                                    await context.bot.send_animation(
                                        chat_id=int(uid),
                                        animation=gif_id,
                                        caption=f"🚀 Buy detected for {name}!\nTx: {tx_hash}"
                                    )
                                else:
                                    await context.bot.send_message(
                                        chat_id=int(uid),
                                        text=f"🚀 Buy detected for {name}!\nTx: {tx_hash}"
                                    )
                                save_data(data)
                                break
            except Exception as e:
                print(f"Error polling DexScreener for {ca}: {e}")

async def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is not set. Create a .env file with BOT_TOKEN=YOUR_TOKEN or export it in the environment.")
        return
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addtoken", addtoken))
    application.add_handler(CommandHandler("removetoken", removetoken))
    application.add_handler(CommandHandler("addgif", addgif))
    application.add_handler(CommandHandler("removegif", removegif))
    application.add_handler(CommandHandler("listtokens", listtokens))
    application.add_handler(CommandHandler("listgifs", listgifs))
    application.add_handler(MessageHandler(filters.ANIMATION, gif_handler))
    application.add_handler(CommandHandler("buy", buy))

    # Schedule DexScreener polling on the job queue
    application.job_queue.run_repeating(poll_dexscreener_task, interval=POLL_INTERVAL)

    print("Bot started.")
    await application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())