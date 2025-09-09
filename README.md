# 🔍 Ethereum Mempool Monitor

Система мониторинга мемпула Ethereum в реальном времени с отправкой уведомлений о торговле токенами в Telegram.

## 🚀 Возможности

- **Мониторинг мемпула в реальном времени** через WebSocket Alchemy API
- **Парсинг Uniswap V2 транзакций** - определение покупок и продаж токенов
- **Telegram уведомления** с расширенной информацией и эмодзи
- **Хранение данных** в MongoDB для истории транзакций
- **REST API** для управления и статистики

## 🛠️ Технический стек

- **Backend**: FastAPI + Python
- **Database**: MongoDB
- **WebSocket**: Alchemy Ethereum API
- **Notifications**: Telegram Bot API
- **Deployment**: Kubernetes + Docker

## 📡 API Endpoints

### Управление мониторингом
- `POST /api/start-monitoring` - Запустить мониторинг мемпула
- `POST /api/stop-monitoring` - Остановить мониторинг
- `GET /api/stats` - Получить статистику работы

### Данные транзакций
- `GET /api/transactions?limit=50` - Получить последние транзакции
- `GET /api/` - Статус API

### Telegram интеграция
- `POST /api/test-telegram` - Тестовое сообщение в Telegram

## 🎯 Пример работы

```bash
# Запуск мониторинга
curl -X POST http://localhost:8001/api/start-monitoring

# Получение статистики
curl -X GET http://localhost:8001/api/stats

# Остановка мониторинга
curl -X POST http://localhost:8001/api/stop-monitoring
```

## 📊 Формат Telegram уведомлений

```
🟢 ПОКУПКА ТОКЕНА

🏷️ Токен: Shiba Inu (SHIB)
📄 Контракт: 0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce  
💰 Сумма ETH: 0.1234 ETH
👤 От: 0x1234...abcd
🔗 Транзакция: 0x5678...efgh
⏰ Время: 18:30:45

📊 Посмотреть на DexView
🔍 Etherscan
```

## ⚙️ Конфигурация

Основные переменные окружения в `/app/backend/.env`:

```env
# Alchemy WebSocket для мемпула Ethereum
ALCHEMY_WSS_URL="wss://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"

# Telegram Bot для уведомлений
TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID="YOUR_CHAT_ID"

# MongoDB для хранения данных
MONGO_URL="mongodb://localhost:27017"
DB_NAME="ethereum_mempool_monitor"
```

## 🔧 Мониторинг характеристик

- **Uniswap V2 Router**: `0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D`
- **Поддерживаемые операции**: Buy (ETH→Token), Sell (Token→ETH), Swap (Token→Token)
- **Автоматическое получение информации о токенах** через CoinGecko API
- **Ссылки на DexView и Etherscan** для каждой транзакции

## 📈 Статистика работы

```json
{
  "total_transactions": 1327,
  "successful_parses": 27,
  "failed_parses": 0,
  "telegram_messages_sent": 32,
  "uptime_seconds": 0
}
```

## 🎯 Результат

✅ **Полностью рабочая система** мониторинга мемпула Ethereum  
✅ **Успешная интеграция** с Telegram для уведомлений  
✅ **Реальное время** обработки транзакций Uniswap V2  
✅ **Надежное хранение** данных в MongoDB  
✅ **REST API** для управления и мониторинга  

Система готова к использованию и может обрабатывать тысячи транзакций в реальном времени!
