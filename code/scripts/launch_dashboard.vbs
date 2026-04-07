'' launch_dashboard.vbs — Lance dashboard_server.py sans aucune fenetre
Dim oShell : Set oShell = CreateObject("WScript.Shell")
oShell.Run """C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"" ""C:\Users\averr\MULTI_ASSETS\code\scripts\dashboard_server.py""", 0, False
