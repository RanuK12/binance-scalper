# Binance Scalper

Bot de scalping agresivo para Binance Futures BTC/USDT. Estrategia multi-indicador con gestión de riesgo integrada y dashboard Flask en tiempo real.

## Stack
- Python 3.11+, Asyncio
- Flask + Flask-SocketIO (dashboard web)
- Binance API (futures)
- Deploy: Railway / Fly.io / Render

## Estructura
```
binance-scalper/
├── main.py              # Entry point del bot
├── strategy.py          # Lógica de estrategia multi-indicador
├── exchange.py          # Wrapper de la API de Binance
├── position_manager.py  # Gestión de posiciones y riesgo
├── risk_manager.py      # Reglas de riesgo y stops
├── market_analysis.py   # Análisis técnico
├── learner.py           # Aprendizaje adaptativo
├── dashboard.py         # Dashboard Flask
├── bot_state.py         # Persistencia de estado
├── config.py            # Configuración
└── requirements.txt     # Dependencias
```

## Uso
```bash
cp .env.example .env
# Editar .env con API keys de Binance
python main.py
```

## Disclaimer
Trading de criptomonedas conlleva riesgo sustancial. Este bot es con fines educativos. Usar bajo tu propio riesgo.
