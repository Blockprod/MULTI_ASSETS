# run_trading_bot.ps1
# PowerShell script to run MULTI_SYMBOLS.py with correct PYTHONPATH

$env:PYTHONPATH = "C:\Users\averr\MULTI_ASSETS_V2\src"
python -m trading_bot.MULTI_SYMBOLS
