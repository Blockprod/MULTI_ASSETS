'' launch_ibkr_forex_dashboard.vbs - Lance ibkr_forex_dashboard_server.py sans fenetre
Dim oShell : Set oShell = CreateObject("WScript.Shell")
oShell.Run """C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"" ""C:\Users\averr\MULTI_ASSETS\code\scripts\ibkr_forex_dashboard_server.py""", 0, False