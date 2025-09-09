import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
import websockets
import httpx
from web3 import Web3
from web3.types import TxParams
from fastapi import FastAPI, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pathlib import Path
from pydantic import BaseModel, Field
import uuid
from telegram import Bot
from telegram.constants import ParseMode

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Configuration
ALCHEMY_WSS_URL = os.environ.get('ALCHEMY_WSS_URL')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Initialize Telegram Bot
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

from web3 import Web3
from web3.types import TxParams
from eth_abi import decode

# Web3 instance for transaction decoding  
w3 = Web3()

# Uniswap V2 Router address and ABI signatures
UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"

# ABI function signatures for decoding
UNISWAP_FUNCTION_SIGS = {
    "0x7ff36ab5": {
        "name": "swapExactETHForTokens",
        "inputs": ["uint256", "address[]", "address", "uint256"],
        "swap_type": "buy"
    },
    "0x18cbafe5": {
        "name": "swapExactTokensForETH", 
        "inputs": ["uint256", "uint256", "address[]", "address", "uint256"],
        "swap_type": "sell"
    },
    "0x38ed1739": {
        "name": "swapExactTokensForTokens",
        "inputs": ["uint256", "uint256", "address[]", "address", "uint256"], 
        "swap_type": "swap"
    },
    "0x1f00ca74": {
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "inputs": ["uint256", "address[]", "address", "uint256"],
        "swap_type": "buy"
    },
    "0x791ac947": {
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens", 
        "inputs": ["uint256", "uint256", "address[]", "address", "uint256"],
        "swap_type": "sell"
    }
}

# Create FastAPI app
app = FastAPI(title="Ethereum Mempool Monitor")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Models
class TransactionData(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tx_hash: str
    from_address: str
    to_address: str
    token_address: Optional[str] = None
    token_symbol: Optional[str] = None
    token_name: Optional[str] = None
    amount: Optional[str] = None
    swap_type: str  # "buy" or "sell"
    pool_address: Optional[str] = None
    dexview_link: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MonitorStats(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    total_transactions: int = 0
    successful_parses: int = 0
    failed_parses: int = 0
    telegram_messages_sent: int = 0
    uptime_seconds: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Global variables for monitoring
monitor_stats = MonitorStats()
monitoring_active = False

async def get_token_info(token_address: str) -> Dict:
    """Get token information from contract and external APIs"""
    try:
        if not token_address or token_address == "0x" + "0" * 40:
            return {
                "symbol": "ETH",
                "name": "Ethereum",
                "address": "0x0000000000000000000000000000000000000000"
            }
            
        # Validate address format
        if len(token_address) != 42 or not token_address.startswith("0x"):
            logger.warning(f"Invalid token address format: {token_address}")
            return {
                "symbol": "UNKNOWN",
                "name": "Unknown Token", 
                "address": token_address
            }
            
        token_address = token_address.lower()
        
        # Common tokens database (most traded tokens)
        common_tokens = {
            "0xdac17f958d2ee523a2206206994597c13d831ec7": {"symbol": "USDT", "name": "Tether USD"},
            "0xa0b86a33e6441e6d9a2e3c8cf8b7f5b6b7f5b0a6": {"symbol": "USDC", "name": "USD Coin"},
            "0x6b175474e89094c44da98b954eedeac495271d0f": {"symbol": "DAI", "name": "Dai Stablecoin"},
            "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": {"symbol": "SHIB", "name": "Shiba Inu"},
            "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": {"symbol": "WBTC", "name": "Wrapped Bitcoin"},
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": {"symbol": "WETH", "name": "Wrapped Ether"},
            "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": {"symbol": "UNI", "name": "Uniswap"},
            "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0": {"symbol": "MATIC", "name": "Polygon"},
            "0xa693b19d2931d498c5b318df961919bb4aee87a5": {"symbol": "UST", "name": "TerraUSD"},
            "0x4e15361fd6b4bb609fa63c81a2be19d873717870": {"symbol": "FTM", "name": "Fantom"},
        }
        
        # Check common tokens first
        if token_address in common_tokens:
            token_info = common_tokens[token_address]
            return {
                "symbol": token_info["symbol"],
                "name": token_info["name"],
                "address": token_address
            }
        
        # Try CoinGecko API for well-known tokens (with rate limiting handling)
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/ethereum/contract/{token_address}",
                    headers={"Accept": "application/json"}
                )
                if response.status_code == 200:
                    data = response.json()
                    symbol = data.get("symbol", "").upper()
                    name = data.get("name", "")
                    if symbol and name:
                        return {
                            "symbol": symbol,
                            "name": name,
                            "address": token_address
                        }
                elif response.status_code == 429:
                    logger.debug(f"CoinGecko rate limited for {token_address}")
                else:
                    logger.debug(f"CoinGecko returned {response.status_code} for {token_address}")
        except Exception as e:
            logger.debug(f"CoinGecko API failed for {token_address}: {e}")
        
        # Try Etherscan API as backup
        try:
            etherscan_api_key = "YourApiKeyToken"  # Free tier available
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"https://api.etherscan.io/api?module=token&action=tokeninfo&contractaddress={token_address}&apikey={etherscan_api_key}"
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "1" and data.get("result"):
                        result = data["result"][0] if isinstance(data["result"], list) else data["result"]
                        symbol = result.get("symbol", "").upper()
                        name = result.get("name", "")
                        if symbol and name:
                            return {
                                "symbol": symbol,
                                "name": name,
                                "address": token_address
                            }
        except Exception as e:
            logger.debug(f"Etherscan API failed for {token_address}: {e}")
            
        # Try 1inch API for token info
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"https://api.1inch.dev/token/v1.2/1/{token_address}",
                    headers={"Accept": "application/json"}
                )
                if response.status_code == 200:
                    data = response.json()
                    symbol = data.get("symbol", "").upper()
                    name = data.get("name", "")
                    if symbol and name:
                        return {
                            "symbol": symbol,
                            "name": name,
                            "address": token_address
                        }
        except Exception as e:
            logger.debug(f"1inch API failed for {token_address}: {e}")
            
        # Generate readable name from contract address
        short_addr = token_address[-8:].upper()
        checksum_chars = token_address[2:8].upper()
        
        return {
            "symbol": f"T{checksum_chars[:4]}",
            "name": f"Token {short_addr}",
            "address": token_address
        }
        
    except Exception as e:
        logger.error(f"Error getting token info for {token_address}: {e}")
        return {
            "symbol": "ERROR",
            "name": "Error Getting Token Info",
            "address": token_address or "unknown"
        }

def parse_swap_transaction(tx_data: Dict) -> Optional[TransactionData]:
    """Parse Uniswap V2 swap transaction with proper ABI decoding"""
    try:
        tx_input = tx_data.get("input", "")
        if not tx_input or len(tx_input) < 10:
            return None
            
        # Check if it's a swap transaction to Uniswap V2 Router
        to_address = tx_data.get("to", "").lower()
        if to_address != UNISWAP_V2_ROUTER.lower():
            return None
            
        method_id = tx_input[:10]
        
        if method_id not in UNISWAP_FUNCTION_SIGS:
            return None
            
        func_info = UNISWAP_FUNCTION_SIGS[method_id]
        swap_type = func_info["swap_type"]
        
        # Get ETH value for buy transactions
        eth_value_wei = int(tx_data.get("value", "0"), 16)
        eth_value = eth_value_wei / 10**18
        
        # Decode function parameters
        token_address = None
        amount_str = "0"
        
        try:
            # Remove method signature (first 4 bytes)
            calldata = bytes.fromhex(tx_input[10:])
            
            # Decode parameters based on function signature
            if swap_type == "buy":  # ETH -> Token swaps
                # swapExactETHForTokens(uint amountOutMin, address[] path, address to, uint deadline)
                if len(calldata) >= 128:  # Minimum data length
                    params = decode(["uint256", "uint256", "uint256", "uint256"], calldata[:128])
                    amount_out_min = params[0]
                    path_offset = params[1] 
                    to_address_param = params[2]
                    deadline = params[3]
                    
                    # Decode path array
                    if len(calldata) > path_offset:
                        path_data = calldata[path_offset:]
                        if len(path_data) >= 32:
                            path_length = int.from_bytes(path_data[:32], 'big')
                            if path_length >= 2 and len(path_data) >= 32 + (path_length * 32):
                                # Get token address (path[1] for ETH->Token)
                                token_bytes = path_data[64:96]  # Second address in path
                                token_address = "0x" + token_bytes[-20:].hex()
                                
                amount_str = str(eth_value)
                
            elif swap_type == "sell":  # Token -> ETH swaps
                # swapExactTokensForETH(uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
                if len(calldata) >= 160:
                    params = decode(["uint256", "uint256", "uint256", "uint256", "uint256"], calldata[:160])
                    amount_in = params[0]
                    amount_out_min = params[1]
                    path_offset = params[2]
                    to_address_param = params[3]
                    deadline = params[4]
                    
                    # Decode path array to get token address (path[0])
                    if len(calldata) > path_offset:
                        path_data = calldata[path_offset:]
                        if len(path_data) >= 64:
                            path_length = int.from_bytes(path_data[:32], 'big')
                            if path_length >= 2 and len(path_data) >= 32 + (path_length * 32):
                                # Get token address (path[0] for Token->ETH)
                                token_bytes = path_data[32:64]  # First address in path
                                token_address = "0x" + token_bytes[-20:].hex()
                    
                    # Convert amount from wei to display value (assume 18 decimals)
                    amount_str = str(amount_in / 10**18)
                    
            else:  # Token -> Token swaps
                if len(calldata) >= 160:
                    params = decode(["uint256", "uint256", "uint256", "uint256", "uint256"], calldata[:160])
                    amount_in = params[0]
                    path_offset = params[2]
                    
                    # Get first token from path
                    if len(calldata) > path_offset:
                        path_data = calldata[path_offset:]
                        if len(path_data) >= 64:
                            path_length = int.from_bytes(path_data[:32], 'big')
                            if path_length >= 2:
                                token_bytes = path_data[32:64]
                                token_address = "0x" + token_bytes[-20:].hex()
                    
                    amount_str = str(amount_in / 10**18)
                    
        except Exception as decode_error:
            logger.debug(f"ABI decode failed, using fallback parsing: {decode_error}")
            
            # Fallback to simple hex parsing
            try:
                data = tx_input[10:]  # Remove method signature
                
                if swap_type == "buy" and eth_value > 0:
                    amount_str = str(eth_value)
                    # Simple path extraction for ETH->Token
                    if len(data) >= 256:
                        # Look for token address in the data (40 hex chars = 20 bytes)
                        for i in range(0, len(data) - 40, 2):
                            addr_candidate = "0x" + data[i:i+40]
                            if len(addr_candidate) == 42 and addr_candidate.startswith("0x"):
                                # Check if it looks like a valid token address (not WETH)
                                if addr_candidate.lower() != "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2":
                                    token_address = addr_candidate.lower()
                                    break
                                    
                elif swap_type == "sell":
                    # For sells, try to get amount from first parameter
                    if len(data) >= 64:
                        try:
                            amount_hex = data[:64]
                            amount_wei = int(amount_hex, 16)
                            amount_str = str(amount_wei / 10**18)
                        except:
                            amount_str = "0"
                            
            except Exception as fallback_error:
                logger.debug(f"Fallback parsing also failed: {fallback_error}")
                if swap_type == "buy":
                    amount_str = str(eth_value)
        
        # Validate token address
        if token_address:
            if len(token_address) != 42 or not token_address.startswith("0x"):
                token_address = None
            elif token_address == "0x0000000000000000000000000000000000000000":
                token_address = None
                
        # Create transaction data
        transaction = TransactionData(
            tx_hash=tx_data.get("hash", ""),
            from_address=tx_data.get("from", ""),
            to_address=to_address,
            token_address=token_address,
            swap_type=swap_type,
            amount=amount_str
        )
        
        return transaction
        
    except Exception as e:
        logger.error(f"Error parsing transaction {tx_data.get('hash', 'unknown')}: {e}")
        return None

async def format_telegram_message(transaction: TransactionData, token_info: Dict) -> str:
    """Format transaction data for Telegram message"""
    try:
        # Determine emoji based on swap type
        if transaction.swap_type == "buy":
            emoji = "🟢"
            action = "ПОКУПКА"
        elif transaction.swap_type == "sell":
            emoji = "🔴"
            action = "ПРОДАЖА"
        else:
            emoji = "🔄"
            action = "ОБМЕН"
            
        # Create DexView link
        dexview_link = f"https://dexview.com/eth/{token_info['address']}"
        
        # Helper function to escape special characters for MarkdownV2
        def escape_md(text: str) -> str:
            special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
            for char in special_chars:
                text = text.replace(char, f'\\{char}')
            return text
            
        # Format message with proper escaping
        token_name = escape_md(token_info['name'])
        token_symbol = escape_md(token_info['symbol'])
        contract_addr = escape_md(token_info['address'])
        from_addr = escape_md(f"{transaction.from_address[:10]}...{transaction.from_address[-6:]}")
        tx_hash = escape_md(f"{transaction.tx_hash[:10]}...{transaction.tx_hash[-6:]}")
        amount = escape_md(transaction.amount[:8])
        timestamp = escape_md(transaction.timestamp.strftime('%H:%M:%S'))
        
        message = f"""
{emoji} *{action} ТОКЕНА*

🏷️ *Токен:* {token_name} \\({token_symbol}\\)
📄 *Контракт:* `{contract_addr}`
💰 *Сумма ETH:* {amount} ETH
👤 *От:* `{from_addr}`
🔗 *Транзакция:* `{tx_hash}`
⏰ *Время:* {timestamp}

📊 [Посмотреть на DexView]({dexview_link})
🔍 [Etherscan](https://etherscan.io/tx/{transaction.tx_hash})
        """
        
        return message.strip()
        
    except Exception as e:
        logger.error(f"Error formatting message: {e}")
        # Fallback to simple message without markdown
        return f"""
{emoji} {action} ТОКЕНА

Токен: {token_info['name']} ({token_info['symbol']})
Контракт: {token_info['address']}
Сумма ETH: {transaction.amount[:8]} ETH
От: {transaction.from_address[:10]}...{transaction.from_address[-6:]}
Транзакция: {transaction.tx_hash[:10]}...{transaction.tx_hash[-6:]}
Время: {transaction.timestamp.strftime('%H:%M:%S')}

DexView: https://dexview.com/eth/{token_info['address']}
Etherscan: https://etherscan.io/tx/{transaction.tx_hash}
        """.strip()

async def send_telegram_message(message: str) -> bool:
    """Send message to Telegram"""
    try:
        # First try with MarkdownV2
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=False
        )
        monitor_stats.telegram_messages_sent += 1
        return True
    except Exception as e:
        logger.warning(f"MarkdownV2 failed: {e}")
        # Fallback to plain text
        try:
            # Remove markdown formatting for plain text
            plain_message = message.replace('*', '').replace('`', '').replace('\\', '')
            await telegram_bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=plain_message,
                disable_web_page_preview=False
            )
            monitor_stats.telegram_messages_sent += 1
            return True
        except Exception as e2:
            logger.error(f"Error sending Telegram message (both attempts failed): {e2}")
            return False

async def process_transaction(tx_data: Dict):
    """Process a single transaction"""
    try:
        monitor_stats.total_transactions += 1
        
        # Parse transaction
        transaction = parse_swap_transaction(tx_data)
        if not transaction:
            return
            
        # Get token info if we have a token address
        if transaction.token_address:
            token_info = await get_token_info(transaction.token_address)
        else:
            # If no token address extracted, use placeholder for demo
            token_info = {
                "symbol": "TOKEN",
                "name": "Unknown Token",
                "address": "0x0000000000000000000000000000000000000000"
            }
        
        transaction.token_address = token_info["address"]
        transaction.token_symbol = token_info["symbol"]
        transaction.token_name = token_info["name"]
        transaction.dexview_link = f"https://dexview.com/eth/{token_info['address']}"
        
        # Save to database
        transaction_dict = transaction.dict()
        # Convert datetime to ISO string for MongoDB
        transaction_dict['timestamp'] = transaction.timestamp.isoformat()
        await db.transactions.insert_one(transaction_dict)
        
        # Format and send Telegram message
        message = await format_telegram_message(transaction, token_info)
        success = await send_telegram_message(message)
        
        if success:
            monitor_stats.successful_parses += 1
            logger.info(f"✅ Processed {transaction.swap_type}: {token_info['symbol']} - {transaction.amount[:6]} ETH")
        else:
            monitor_stats.failed_parses += 1
            logger.warning(f"❌ Failed to send message for {transaction.swap_type}: {token_info['symbol']}")
            
    except Exception as e:
        logger.error(f"Error processing transaction: {e}")
        monitor_stats.failed_parses += 1

async def monitor_mempool():
    """Main mempool monitoring function"""
    global monitoring_active
    monitoring_active = True
    
    logger.info("🚀 Starting Ethereum mempool monitoring...")
    
    # Send startup notification
    startup_message = """
🤖 *МОНИТОР МЕМПУЛА ЗАПУЩЕН*

✅ Подключение к Ethereum мемпулу активно
🎯 Отслеживаем Uniswap V2 свапы
📱 Уведомления настроены

_Начинаем мониторинг торговли токенами\\.\\.\\._
    """
    
    await send_telegram_message(startup_message)
    
    while monitoring_active:
        try:
            async with websockets.connect(ALCHEMY_WSS_URL) as websocket:
                # Subscribe to pending transactions
                subscribe_message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["alchemy_pendingTransactions"]
                }
                
                await websocket.send(json.dumps(subscribe_message))
                logger.info("📡 Subscribed to pending transactions")
                
                async for message in websocket:
                    if not monitoring_active:
                        break
                        
                    try:
                        data = json.loads(message)
                        if "params" in data and "result" in data["params"]:
                            tx_data = data["params"]["result"]
                            await process_transaction(tx_data)
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        
        except websockets.exceptions.ConnectionClosed:
            logger.warning("🔌 WebSocket connection closed, reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ WebSocket error: {e}")
            await asyncio.sleep(10)

# API Routes
@app.get("/api/")
async def root():
    return {"message": "Ethereum Mempool Monitor API", "status": "running"}

@app.get("/api/stats")
async def get_stats():
    """Get monitoring statistics"""
    return monitor_stats.dict()

@app.post("/api/start-monitoring")
async def start_monitoring(background_tasks: BackgroundTasks):
    """Start mempool monitoring"""
    global monitoring_active
    
    if monitoring_active:
        return {"message": "Monitoring already active", "status": "running"}
    
    background_tasks.add_task(monitor_mempool)
    return {"message": "Monitoring started", "status": "starting"}

@app.post("/api/stop-monitoring")
async def stop_monitoring():
    """Stop mempool monitoring"""
    global monitoring_active
    monitoring_active = False
    
    # Send stop notification
    stop_message = """
🛑 *МОНИТОР МЕМПУЛА ОСТАНОВЛЕН*

📊 Статистика сессии:
• Всего транзакций: {total}
• Успешно обработано: {success}
• Ошибок: {errors}
• Отправлено сообщений: {sent}

_Мониторинг остановлен\\._
    """.format(
        total=monitor_stats.total_transactions,
        success=monitor_stats.successful_parses,
        errors=monitor_stats.failed_parses,
        sent=monitor_stats.telegram_messages_sent
    )
    
    await send_telegram_message(stop_message)
    return {"message": "Monitoring stopped", "status": "stopped"}

@app.get("/api/transactions")
async def get_recent_transactions(limit: int = 50):
    """Get recent transactions"""
    transactions = await db.transactions.find().sort("timestamp", -1).limit(limit).to_list(limit)
    return [TransactionData(**tx) for tx in transactions]

@app.post("/api/test-telegram")
async def test_telegram():
    """Test Telegram integration"""
    test_message = """
🧪 *ТЕСТ ИНТЕГРАЦИИ*

✅ Telegram Bot работает корректно
🔗 Подключение к чату установлено
📱 Уведомления доставляются

_Тестовое сообщение от Ethereum Mempool Monitor_
    """
    
    success = await send_telegram_message(test_message)
    if success:
        return {"message": "Test message sent successfully", "status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test message")

# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize application"""
    logger.info("🚀 Starting Ethereum Mempool Monitor API")
    
    # Test Telegram connection
    try:
        await telegram_bot.get_me()
        logger.info("✅ Telegram bot connection successful")
    except Exception as e:
        logger.error(f"❌ Telegram bot connection failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global monitoring_active
    monitoring_active = False
    client.close()
    logger.info("👋 Shutting down Ethereum Mempool Monitor")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)