# Bot de notícias de mercado para Telegram

Monitora feeds de notícias de mercado (FXStreet, Investing.com e Reuters) e envia
as notícias novas para um canal, grupo ou conversa privada no Telegram.

- Sem dependências externas: só Python 3.10+ (testado no Python 3.12).
- Deduplicação por GUID: cada notícia é enviada uma única vez.
- Rajadas são agrupadas em um resumo para não inundar o chat.
- Filtros opcionais por palavra-chave (ignoram acentos e maiúsculas).
- Tratamento de HTTP 429, cache `304 Not Modified` e novas tentativas.
- Logs com rotação em `bot.log`.

> **Reuters:** a Reuters desativou os feeds RSS próprios (retornam 401/404).
> A fonte `reuters` usa o índice do Google News (`site:reuters.com`) e o bot
> decodifica cada link para a URL original em `reuters.com`, preservando o
> preview das matérias.

## 1. Criar o bot no Telegram

1. Abra o **@BotFather** no Telegram e envie `/newbot`.
2. Escolha um nome e um usuário (precisa terminar em `bot`).
3. Copie o **token** mostrado (algo como `123456789:AAH...`).

### Adicionar o bot ao destino

- **Canal:** Administradores → Adicionar administrador → escolha o bot.
  Marque a permissão de **publicar mensagens**.
- **Grupo:** adicione o bot ao grupo. Se quiser que ele apareça em `--chats`,
  envie `/start@seu_bot` no grupo (o modo privacidade do bot pode impedir que
  ele veja mensagens normais; comandos funcionam).
- **Conversa privada:** abra o bot e envie `/start`.

## 2. Descobrir o `chat_id`

Para **canais públicos**, o próprio `@nome_do_canal` funciona como destino.

Para canais privados, grupos e conversas privadas:

```powershell
python bot.py --chats
```

O comando lista `id`, tipo e nome de cada chat que o bot viu. Se a lista estiver
vazia, mande uma mensagem no chat (ou publique no canal) e rode de novo.

Copie o número — para canais e supergrupos ele começa com `-100`.

## 3. Configurar

O arquivo `.env` já existe neste projeto. Abra e preencha:

```ini
TELEGRAM_BOT_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=@meu_canal
```

Os demais campos já vêm com valores recomendados. Veja a tabela de
configurações mais abaixo.

## 4. Testar

```powershell
cd C:\Users\adeil\market-news-telegram-bot

python bot.py --test      # envia uma mensagem de teste
python bot.py --check     # valida os dois feeds RSS
python bot.py --preview 3 # mostra 3 mensagens de exemplo por fonte
```

> O Financial Juice limita requisições por IP. Se `--check` e `--preview`
> forem executados em sequência, é normal receber `HTTP 429`. Aguarde 1–2
> minutos e tente de novo; o bot já trata isso sozinho em execução normal.

## 5. Rodar

```powershell
python bot.py
```

Ou dê dois cliques em `run.bat`.

Na **primeira execução**, o bot registra o feed atual como "já visto" e não
envia nada. Isso evita despejar 100 notícias antigas no canal. As notícias que
chegarem a partir daí são enviadas normalmente.

Se quiser receber as últimas notícias logo no primeiro ciclo:

```ini
SEND_BACKLOG=true
BACKLOG_LIMIT=5
```

Depois disso, rode `python bot.py --reset-state` para que a primeira execução
aconteça novamente.

## Comandos

| Comando | O que faz |
| --- | --- |
| `python bot.py` | Roda continuamente. |
| `python bot.py --once` | Executa um ciclo e sai. |
| `python bot.py --test` | Envia uma mensagem de teste. |
| `python bot.py --chats` | Lista chat_ids que o bot consegue ver. |
| `python bot.py --check` | Testa os feeds RSS e a configuração. |
| `python bot.py --preview 3` | Mostra mensagens de exemplo, sem enviar. |
| `python bot.py --once --dry-run` | Simula um ciclo, sem enviar e sem alterar o estado. |
| `python bot.py --reset-state` | Apaga o histórico de itens vistos. |

Use `-v` para logs detalhados.

## Configurações (`.env`)

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | Token do @BotFather. Obrigatório. |
| `TELEGRAM_CHAT_ID` | — | `@canal` ou id numérico. Obrigatório. |
| `SOURCES` | `fj,fxstreet` | Fontes ativas (`fj`, `fxstreet`, `reuters`, `investing`...). |
| `POLL_SECONDS` | `120` | Intervalo entre ciclos (mínimo 30). |
| `SEND_DELAY_SECONDS` | `3.0` | Pausa entre mensagens enviadas. |
| `SINGLE_MAX` | `4` | Acima disso, as notícias do ciclo viram um resumo. |
| `MAX_ITEMS_PER_CYCLE` | `40` | Teto de itens por fonte/ciclo. |
| `MAX_MESSAGES_PER_CYCLE` | `12` | Teto de mensagens por fonte/ciclo. |
| `FETCH_TIMEOUT` | `30` | Timeout de cada requisição RSS, em segundos. |
| `SEND_BACKLOG` | `false` | Envia as últimas notícias na primeira execução. |
| `BACKLOG_LIMIT` | `5` | Quantas notícias enviar no backlog (máx. 50). |
| `INCLUDE_SUMMARY` | `true` | Inclui o resumo do RSS quando existir. |
| `INCLUDE_KEYWORDS` | vazio | Só envia se contiver alguma palavra. |
| `EXCLUDE_KEYWORDS` | vazio | Não envia se contiver alguma palavra. |
| `TIMEZONE` | `local` | `local`, `UTC`, `UTC-3` ou `America/Sao_Paulo`. |
| `DISABLE_NOTIFICATION` | `false` | `true` envia silenciosamente. |
| `DISABLE_PREVIEW` | `true` | `false` mostra o card do link (preview) no Telegram. |
| `PREVIEW_LARGE` | `true` | Usa imagem grande no card do link. |
| `PREVIEW_DIGEST` | `false` | `true` mostra preview também nos resumos (digest). |
| `TRANSLATE_ENABLED` | `false` | `true` traduz manchetes e resumos para português. |
| `TRANSLATE_TARGET` | `pt-BR` | Idioma de destino da tradução. |
| `TRANSLATE_MAX_PER_CYCLE` | `20` | Teto de textos traduzidos por ciclo. |
| `REUTERS_IMPORTANCE` | `important` | `important` = só macro/mercado; `all` = tudo (inclui esporte). |
| `REUTERS_INCLUDE_KEYWORDS` | vazio | Sobrescreve a lista de termos da Reuters. |
| `RESOLVE_LINKS` | `true` | Converte link do Google News na URL real (melhora o preview). |
| `RESOLVE_LINKS_MAX_PER_CYCLE` | `10` | Teto de links decodificados por ciclo. |
| `STATE_FILE` | `state.json` | Histórico de itens vistos. |
| `LOG_FILE` | `bot.log` | Arquivo de log. |

As variáveis de ambiente do sistema têm prioridade sobre o `.env`.

### Filtros

A comparação é feita no título + resumo, sem diferenciar maiúsculas e acentos.
Separe por vírgula.

```ini
# Foco em macro/forex
INCLUDE_KEYWORDS=dollar,euro,fed,ecb,boe,juros,inflacao,payrolls,cpi,gold,oil

# Ruído que você não quer
EXCLUDE_KEYWORDS=futebol,celebridade,ufc
```

Com filtros ativos, notícias que não passam são marcadas como vistas para não
aparecerem depois. Se mudar os filtros e quiser reavaliar o feed atual, rode
`python bot.py --reset-state` e use `SEND_BACKLOG=true`.

> O feed do Financial Juice é bem volumoso e inclui geopolítica geral. Sem
> filtros, o canal pode receber muitas mensagens por dia. Ajuste
> `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS` ou deixe apenas `SOURCES=fxstreet`.

## Rodar automaticamente no Windows

### Opção A: pasta Inicializar

1. Pressione `Win + R`, digite `shell:startup` e pressione Enter.
2. Crie um atalho para `start-hidden.vbs` nesta pasta.

O script abre o bot sem janela; os logs continuam em `bot.log`.

### Opção B: Agendador de Tarefas

1. Abra o **Agendador de Tarefas** → **Criar Tarefa**.
2. Em **Geral**, escolha "Executar somente quando o usuário estiver conectado".
3. Em **Disparadores**, adicione **Ao fazer logon**.
4. Em **Ações**, adicione:
   - Programa: `wscript.exe`
   - Argumentos: `"C:\Users\adeil\market-news-telegram-bot\start-hidden.vbs"`
   - Iniciar em: `C:\Users\adeil\market-news-telegram-bot`
5. Salve e teste com **Executar**.

Ou, no PowerShell:

```powershell
schtasks /Create /TN "Telegram Market News Bot" /TR 'wscript.exe "C:\Users\adeil\market-news-telegram-bot\start-hidden.vbs"' /SC ONLOGON /RL LIMITED /F
```

## Como funciona

1. A cada `POLL_SECONDS`, busca os RSS ativos.
2. Compara os GUIDs com o `state.json` e separa o que é novo.
3. Aplica os filtros.
4. Envia cada notícia (ou um digest, se houver muitas de uma vez).
5. Marca os itens como vistos e salva o estado.

Se o feed responder `304 Not Modified`, nada é baixado de novo. Se o Telegram
ou o RSS falhar, o bot registra o erro, espera com backoff exponencial (até 30
min) e tenta de novo — sem derrubar o processo.

## Solução de problemas

**`HTTP 429` no Financial Juice**
O IP foi limitado temporariamente. Aumente `POLL_SECONDS` para `180` ou `300`.
Evite rodar `--check`, `--preview` e o bot em sequência imediata. O bot respeita
o `Retry-After` e tenta novamente sozinho.

**`chat not found` / `Bad Request: chat_id is empty`**
Confira o `TELEGRAM_CHAT_ID`. Para canais privados e supergrupos, o id começa
com `-100`. O bot precisa ser administrador do canal com permissão de publicar.

**`409 Conflict` ao usar `--chats`**
Existe um webhook configurado no bot. Remova com:
`https://api.telegram.org/bot<TOKEN>/deleteWebhook`
(depois rode `--chats` novamente).

**O bot não envia nada**
Na primeira execução isso é esperado (o feed atual é marcado como visto).
Confira `bot.log`, os filtros e se o bot está no destino. Para forçar um teste,
use `python bot.py --test`.

**Mensagens duplicadas**
Provavelmente há duas instâncias rodando (por exemplo, `run.bat` e uma tarefa
agendada ao mesmo tempo). Deixe apenas uma.

**Horário errado**
Ajuste `TIMEZONE=America/Sao_Paulo` ou `TIMEZONE=UTC-3`. No Windows, nomes IANA
podem exigir o pacote `tzdata`; para os principais fusos brasileiros o bot tem
fallback embutido.

**Token vazou**
Revogue em @BotFather → `/revoke` e atualize o `.env`.

## Estrutura

```
market-news-telegram-bot/
├── bot.py            # o bot completo, em um único arquivo (só biblioteca padrão)
├── requirements.txt  # sem dependências obrigatórias (tzdata opcional no Windows)
├── .env              # suas configurações e segredos
├── .env.example      # modelo comentado
├── state.json        # criado automaticamente (itens vistos)
├── bot.log           # criado automaticamente
├── run.bat           # inicia com janela (bom para ver logs)
└── start-hidden.vbs  # inicia sem janela (autostart)
```

## Aviso

Este projeto usa feeds RSS públicos para uso pessoal. Respeite os termos de uso
do Financial Juice e do FXStreet. As notícias podem ter atraso e não constituem
recomendação de investimento.
