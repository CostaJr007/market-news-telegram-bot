# Guia de Deploy na VPS (Linux / Ubuntu / Debian)

O bot foi construído usando **apenas a biblioteca padrão do Python 3** (sem dependências externas como `pip` ou pacotes pesados). Isso significa que ele roda de forma leve, consome menos de 30MB de memória RAM e não tem risco de conflito de versões.

---

## Método 1: Instalação Automática via Systemd (Recomendado)

O Systemd garante que o bot:
1. Inicie automaticamente junto com a VPS.
2. Reinicie sozinho se houver queda ou reinicialização.
3. Grave logs organizados no `journalctl`.

### Passo 1: Enviar os arquivos para sua VPS

No seu computador local (PowerShell ou terminal):
```powershell
# Exemplo usando SCP (substitua pelo IP e usuário da sua VPS):
scp -r C:\Users\adeil\market-news-telegram-bot root@SEU_IP_VPS:/opt/market-news-telegram-bot
```
*(Ou envie o arquivo `market-news-telegram-bot.zip` via FileZilla / WinSCP e descompacte com `unzip` na VPS).*

### Passo 2: Executar o instalador na VPS

Acesse a VPS via SSH:
```bash
ssh root@SEU_IP_VPS
cd /opt/market-news-telegram-bot
bash deploy.sh
```

O script `deploy.sh` executa automaticamente:
- Verificação do Python 3 e fuso horário.
- Bateria de testes automatizados (`test_bot.py`).
- Instalação e habilitação do serviço `market-news-bot.service`.
- Inicialização do bot em segundo plano.

---

## Método 2: Execução com Docker (Opcional)

Se você utiliza Docker na sua VPS:

```bash
cd /opt/market-news-telegram-bot
docker compose up -d --build
```

---

## Comandos Úteis na VPS

| Ação | Comando |
| :--- | :--- |
| **Acompanhar logs ao vivo** | `journalctl -u market-news-bot -f` |
| **Verificar status do serviço** | `systemctl status market-news-bot` |
| **Reiniciar o bot** | `systemctl restart market-news-bot` |
| **Parar o bot** | `systemctl stop market-news-bot` |
| **Testar conexão no Telegram** | `python3 bot.py --test` |
| **Simular agenda econômica** | `python3 bot.py --calendar --dry-run` |
