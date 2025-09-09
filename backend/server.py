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

# Web3 instance for transaction decoding
w3 = Web3()

# Uniswap V2 Router address and swap signature
UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
SWAP_EXACT_ETH_FOR_TOKENS = "0x7ff36ab5"
SWAP_EXACT_TOKENS_FOR_ETH = "0x18cbafe5"
SWAP_EXACT_TOKENS_FOR_TOKENS = "0x38ed1739"

# ERC20 Transfer signature
TRANSFER_SIGNATURE = "0xa9059cbb"

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
    """Get token information from contract"""
    try:
        # Use a simple API to get token info (you can also use direct Web3 calls)
        async with httpx.AsyncClient() as client:
            # Try to get token info from a public API
            response = await client.get(f"https://api.coingecko.com/api/v3/coins/ethereum/contract/{token_address}")
            if response.status_code == 200:
                data = response.json()
                return {
                    "symbol": data.get("symbol", "UNKNOWN").upper(),
                    "name": data.get("name", "Unknown Token"),
                    "address": token_address
                }
    except Exception as e:
        logger.error(f"Error getting token info for {token_address}: {e}")
    
    # Fallback - return basic info
    return {
        "symbol": "UNKNOWN",
        "name": "Unknown Token",
        "address": token_address
    }

def parse_swap_transaction(tx_data: Dict) -> Optional[TransactionData]:
    """Parse Uniswap V2 swap transaction"""
    try:
        tx_input = tx_data.get("input", "")
        if not tx_input or len(tx_input) < 10:
            return None
            
        # Check if it's a swap transaction to Uniswap V2 Router
        to_address = tx_data.get("to", "").lower()
        if to_address != UNISWAP_V2_ROUTER.lower():
            return None
            
        method_id = tx_input[:10]
        
        # Common Uniswap V2 method signatures
        uniswap_methods = {
            "0x7ff36ab5": "swapExactETHForTokens",           # ETH -> Token
            "0x18cbafe5": "swapExactTokensForETH",           # Token -> ETH  
            "0x38ed1739": "swapExactTokensForTokens",        # Token -> Token
            "0x8803dbee": "swapTokensForExactETH",           # Token -> ETH (exact out)
            "0x4a25d94a": "swapTokensForExactTokens",        # Token -> Token (exact out)
            "0x1f00ca74": "swapExactETHForTokensSupportingFeeOnTransferTokens", # ETH -> Token (fee)
            "0x791ac947": "swapExactTokensForETHSupportingFeeOnTransferTokens", # Token -> ETH (fee)
            "0x5c11d795": "swapExactTokensForTokensSupportingFeeOnTransferTokens" # Token -> Token (fee)
        }
        
        if method_id not in uniswap_methods:
            return None
            
        method_name = uniswap_methods[method_id]
        
        # Determine swap type based on method
        if "ETHForTokens" in method_name:
            swap_type = "buy"  # ETH -> Token
        elif "TokensForETH" in method_name:
            swap_type = "sell"  # Token -> ETH
        else:
            swap_type = "swap"  # Token -> Token
            
        # Get ETH value
        eth_value = int(tx_data.get("value", "0"), 16) / 10**18
        
        # Try to extract token address from transaction input
        token_address = None
        try:
            # For simplicity, we'll decode the path parameter from common swap methods
            # This is a simplified approach - full implementation would need proper ABI decoding
            if len(tx_input) > 138:  # Enough data for path parameter
                # Extract path array (simplified)
                if swap_type == "buy":
                    # ETH -> Token: path[1] is token address
                    token_address = "0x" + tx_input[138:178]  # 20 bytes = 40 hex chars
                elif swap_type == "sell":
                    # Token -> ETH: path[0] is token address  
                    token_address = "0x" + tx_input[74:114]   # First address in path
        except:
            pass
            
        # Create transaction data
        transaction = TransactionData(
            tx_hash=tx_data.get("hash", ""),
            from_address=tx_data.get("from", ""),
            to_address=to_address,
            token_address=token_address,
            swap_type=swap_type,
            amount=str(eth_value)
        )
        
        return transaction
        
    except Exception as e:
        logger.error(f"Error parsing transaction: {e}")
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
        
        # Format message with emojis and markdown
        message = f"""
{emoji} *{action} ТОКЕНА*

🏷️ *Токен:* {token_info['name']} \\({token_info['symbol']}\\)
📄 *Контракт:* `{token_info['address']}`
💰 *Сумма ETH:* {transaction.amount[:8]} ETH
👤 *От:* `{transaction.from_address[:10]}...{transaction.from_address[-6:]}`
🔗 *Транзакция:* `{transaction.tx_hash[:10]}...{transaction.tx_hash[-6:]}`
⏰ *Время:* {transaction.timestamp.strftime('%H:%M:%S')}

📊 [Посмотреть на DexView]({dexview_link})
🔍 [Etherscan](https://etherscan.io/tx/{transaction.tx_hash})
        """
        
        return message.strip()
        
    except Exception as e:
        logger.error(f"Error formatting message: {e}")
        return f"❌ Ошибка форматирования сообщения для транзакции {transaction.tx_hash}"

async def send_telegram_message(message: str) -> bool:
    """Send message to Telegram"""
    try:
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=False
        )
        monitor_stats.telegram_messages_sent += 1
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

async def process_transaction(tx_data: Dict):
    """Process a single transaction"""
    try:
        monitor_stats.total_transactions += 1
        
        # Parse transaction
        transaction = parse_swap_transaction(tx_data)
        if not transaction:
            return
            
        # Get token info (assume first token in path for simplicity)
        # In real implementation, you'd decode the transaction input to get exact token addresses
        token_address = "0xA0b86a33E6441E6d9a2e3c8cf8b7f5b6b7f5b0a6"  # Placeholder
        token_info = await get_token_info(token_address)
        
        transaction.token_address = token_info["address"]
        transaction.token_symbol = token_info["symbol"]
        transaction.token_name = token_info["name"]
        transaction.dexview_link = f"https://dexview.com/eth/{token_address}"
        
        # Save to database
        await db.transactions.insert_one(transaction.dict())
        
        # Format and send Telegram message
        message = await format_telegram_message(transaction, token_info)
        success = await send_telegram_message(message)
        
        if success:
            monitor_stats.successful_parses += 1
            logger.info(f"Processed swap: {transaction.swap_type} - {token_info['symbol']}")
        else:
            monitor_stats.failed_parses += 1
            
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