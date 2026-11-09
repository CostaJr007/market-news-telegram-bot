@echo off
setlocal
cd /d "%~dp0"
title Bot de noticias - Telegram
python bot.py %*
echo.
echo O bot foi encerrado. Pressione uma tecla para fechar.
pause >nul
