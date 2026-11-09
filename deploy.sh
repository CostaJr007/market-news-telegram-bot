#!/usr/bin/env bash
set -e

echo "=============================================="
echo " Instalador Telegram Market News Bot - Linux VPS"
echo "=============================================="

# Verifica se está executando como root
if [ "$EUID" -ne 0 ]; then
  echo "[ERRO] Execute este script como root ou com sudo:"
  echo "  sudo bash deploy.sh"
  exit 1
fi

APP_DIR="/opt/market-news-telegram-bot"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Instala python3 e tzdata se necessário
echo "[1/5] Verificando dependências do sistema..."
if ! command -v python3 &> /dev/null; then
    echo "Instalando Python 3..."
    apt-get update -qq && apt-get install -y -qq python3 python3-tzdata ca-certificates
fi

# Cria diretório de destino se for diferente do atual
echo "[2/5] Organizando diretório da aplicação em $APP_DIR..."
if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
    cp -r "$SOURCE_DIR"/* "$APP_DIR"/
    [ -f "$SOURCE_DIR/.env" ] && cp "$SOURCE_DIR/.env" "$APP_DIR/.env"
fi

cd "$APP_DIR"

# Garante permissões de execução
chmod +x bot.py test_bot.py test_pipeline.py 2>/dev/null || true

# Executa bateria de testes automatizados
echo "[3/5] Executando bateria de testes do pipeline e do sistema..."
python3 test_pipeline.py
python3 test_bot.py

# Configura o serviço no systemd
echo "[4/5] Configurando serviço do systemd..."
cp market-news-bot.service /etc/systemd/system/market-news-bot.service

systemctl daemon-reload
systemctl enable market-news-bot.service

# Inicia o serviço
echo "[5/5] Iniciando o bot em segundo plano..."
systemctl restart market-news-bot.service

echo ""
echo "=============================================="
echo "       BOT INSTALADO E ATIVO NA VPS!          "
echo "=============================================="
systemctl status market-news-bot.service --no-pager
echo ""
echo "Comandos úteis para gerenciar:"
echo "  • Ver logs ao vivo:    journalctl -u market-news-bot -f"
echo "  • Status do bot:       systemctl status market-news-bot"
echo "  • Reiniciar o bot:     systemctl restart market-news-bot"
echo "  • Parar o bot:         systemctl stop market-news-bot"
