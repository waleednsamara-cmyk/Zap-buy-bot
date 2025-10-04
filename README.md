## Zap-buy-bot

Minimal Telegram buy bot for Solana with Jupiter swap integration and DexScreener buy alerts. Users can:
- Add Solana token mints to watch; receive alerts on new buys
- Upload up to 5 GIFs that are randomly sent with alerts
- Execute buys via `/buy <mint> <amount_in_SOL>` using a configured wallet

### Setup
1. Create a bot with BotFather and copy the token.
2. Create a `.env` file in the project root:

```
BOT_TOKEN=123456:ABCDEF...
RPC_URL=https://api.mainnet-beta.solana.com
WALLET_SECRET_KEY=[1,2,3,...,64]  # or base58 string
SLIPPAGE_BPS=100
POLL_INTERVAL=60
```

3. Install dependencies:

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_Version4.txt
```

### Run

```
python solana_buy_bot_Version4.py
```

### Commands
- `/start` welcome and help
- `/addtoken <mint> <name>` add token to watchlist
- `/removetoken <mint>` remove token
- `/listtokens` list tokens
- `/addgif` then send a GIF file to store
- `/removegif <index>` remove GIF
- `/listgifs` list GIFs
- `/buy <mint> <amount_in_SOL>` execute swap using Jupiter

### Notes
- Uses Jupiter v6 quote/swap APIs and signs locally with the provided wallet
- Stores simple user data in `data.json`