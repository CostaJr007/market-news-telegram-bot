#!/usr/bin/env python3
"""
Bot de notícias de mercado para Telegram.

Fontes (RSS público):
  - Financial Juice: https://www.financialjuice.com/feed.ashx?xy=rss
  - FXStreet:        https://www.fxstreet.com/rss/news

Sem dependências externas: usa somente a biblioteca padrão do Python 3.10+.

Comandos:
  python bot.py              # roda continuamente
  python bot.py --once       # executa apenas um ciclo
  python bot.py --test       # envia uma mensagem de teste
  python bot.py --chats      # mostra os chat_ids que o bot consegue ver
  python bot.py --check      # testa as fontes RSS e a configuração
  python bot.py --preview 3  # mostra 3 mensagens de exemplo de cada fonte
  python bot.py --reset-state
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

APP_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("newsbot")
CACHE_DIR = APP_DIR / ".cache"

MAX_TELEGRAM_TEXT = 3800
STATE_VERSION = 2


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    key: str
    name: str
    emoji: str
    url: str


SOURCES: dict[str, Source] = {
    "fj": Source(
        key="fj",
        name="Financial Juice",
        emoji="🟠",
        url="https://www.financialjuice.com/feed.ashx?xy=rss",
    ),
    "fxstreet": Source(
        key="fxstreet",
        name="FXStreet",
        emoji="🔵",
        url="https://www.fxstreet.com/rss/news",
    ),
    "investing": Source(
        key="investing",
        name="Investing.com (Forex)",
        emoji="🟢",
        url="https://www.investing.com/rss/news_1.rss",
    ),
    "investing_stocks": Source(
        key="investing_stocks",
        name="Investing.com (Ações)",
        emoji="🟡",
        url="https://www.investing.com/rss/news_25.rss",
    ),
    "investing_br": Source(
        key="investing_br",
        name="Investing.com Brasil (Economia)",
        emoji="🇧🇷",
        url="https://br.investing.com/rss/news_14.rss",
    ),
    "investing_br_forex": Source(
        key="investing_br_forex",
        name="Investing.com Brasil (Câmbio)",
        emoji="💵",
        url="https://br.investing.com/rss/news_1.rss",
    ),
    "investing_br_stocks": Source(
        key="investing_br_stocks",
        name="Investing.com Brasil (Ações)",
        emoji="📈",
        url="https://br.investing.com/rss/news_25.rss",
    ),
    "yahoo": Source(
        key="yahoo",
        name="Yahoo Finance",
        emoji="🟣",
        url="https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^DJI,^IXIC,GC=F,CL=F,BRL=X&region=US&lang=en-US",
    ),
    # A Reuters desativou os feeds RSS próprios (401/404). Usamos o índice do
    # Google News, que publica as matérias da Reuters em tempo real.
    "reuters": Source(
        key="reuters",
        name="Reuters",
        emoji="📰",
        url=(
            "https://news.google.com/rss/search?"
            "q=site%3Areuters.com+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"
        ),
    ),
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 MarketNewsBot/1.0"
)

# Prefixos de título que indicam notícia urgente/importantes no FXStreet.
FXSTREET_BREAKING_PREFIXES = ("breaking:", "live:", "update:", "flash:")

# Termos que identificam notícias de mercado importantes no FXStreet.
# Usados quando FXSTREET_IMPORTANCE=important (padrão).
FXSTREET_IMPORTANT_KEYWORDS = (
    "fed", "fomc", "powell", "ecb", "lagarde", "boe", "boj", "ueda", "central bank",
    "rate decision", "rate hike", "rate cut", "hike", "cut", "inflation", "cpi",
    "ppi", "payrolls", "nfp", "unemployment", "jobs report", "gdp", "recession",
    "tariff", "war", "ceasefire", "sanctions", "crisis", "oil", "opec", "crude",
    "gold", "bitcoin", "treasury", "shutdown", "default", "stimulus", "trump",
    "china", "iran", "israel", "ukraine", "taiwan", "retail sales", "pmi", "ism",
    "yield", "nasdaq", "s&p",
)

# Termos que identificam categorias do Financial Juice (Forex, Índices, Macro).
FJ_FOREX_KEYWORDS = (
    "fx", "forex", "currency", "currencies", "dollar", "dxy", "greenback",
    "euro", "eur", "yen", "jpy", "pound", "gbp", "sterling", "yuan", "cny", "cnh",
    "aud", "cad", "chf", "nzd", "ruble", "rub", "real", "brl",
    "rba", "pboc", "boj", "ecb", "boe", "snb", "boc",
    "eur/usd", "gbp/usd", "usd/jpy", "aud/usd", "usd/cad", "usd/chf", "nzd/usd",
    "eur/gbp", "eur/jpy", "gbp/jpy", "aud/nzd", "usd/cnh", "usd/brl",
    "federal reserve", "reserve bank of australia", "bank of japan", "bank of england",
    "european central bank", "peoples bank of china", "swiss national bank", "bank of canada",
)

FJ_INDEX_KEYWORDS = (
    "s&p", "s&p 500", "sp500", "nasdaq", "dow", "dow jones", "djia", "russell", "russell 2000",
    "dax", "ftse", "cac", "cac 40", "nikkei", "hang seng", "shanghai composite", "csi 300",
    "ibovespa", "bovespa", "vix", "futures", "wall street", "stock market",
    "equity market", "equities", "stock index", "stock indices", "global stocks",
)

FJ_MACRO_KEYWORDS = (
    "fed", "fomc", "powell", "lagarde", "bailey", "ueda", "central bank", "monetary policy",
    "interest rate", "interest rates", "rate decision", "rate hike", "rate cut",
    "rate hikes", "rate cuts", "hike", "cut", "tightening", "easing",
    "inflation", "cpi", "ppi", "pce", "gdp", "pmi", "ism", "payrolls", "nfp", "nonfarm",
    "unemployment", "jobless", "labor market", "labour market", "retail sales",
    "trade balance", "consumer confidence", "sentiment", "industrial production",
    "treasury", "treasuries", "yield", "yields", "bonds", "sovereign debt", "debt ceiling",
    "fiscal deficit", "recession", "stagflation", "soft landing", "stimulus",
    "tariff", "tariffs", "trade war", "sanctions", "geopolitic", "war", "ceasefire",
    "opec", "crude", "oil", "brent", "wti", "gold", "silver", "natural gas", "lng",
    "white house", "trump", "biden", "strait of hormuz", "financial stability",
)

# Filtro da Reuters (via Google News): foco em mercado/macro. Política genérica
# (Trump, eleições, Casa Branca) fica de fora para não poluir o canal.
REUTERS_MARKET_KEYWORDS = (
    "dollar", "euro", "yen", "pound", "sterling", "forex", "currency", "currencies",
    "fed", "fomc", "powell", "ecb", "lagarde", "boj", "boe", "snb", "rba", "pboc",
    "central bank", "interest rate", "rate cut", "rate hike", "rate decision",
    "inflation", "cpi", "ppi", "pce", "deflation", "stagflation",
    "payrolls", "nfp", "nonfarm", "jobs report", "unemployment", "jobless",
    "gdp", "recession", "soft landing", "stimulus",
    "stocks", "shares", "equities", "s&p", "nasdaq", "dow", "wall street",
    "stock market", "bond", "bonds", "yield", "yields", "treasury", "debt ceiling",
    "oil", "crude", "brent", "wti", "opec", "gold", "silver", "copper", "natural gas",
    "bitcoin", "crypto", "cryptocurrency", "ethereum",
    "tariff", "tariffs", "trade war", "sanctions",
    "war", "ceasefire", "strait of hormuz", "iran", "israel", "ukraine", "taiwan",
    "retail sales", "pmi", "ism", "consumer confidence", "industrial production",
    "trade balance", "emerging markets", "yuan", "real", "brl", "ibovespa",
)

# Sinônimos aceitos em CALENDAR_IMPACTS (português/inglês).
IMPACT_ALIASES = {
    "high": "high",
    "alto": "high",
    "alta": "high",
    "medium": "medium",
    "medio": "medium",
    "media": "medium",
    "baixo": "low",
    "low": "low",
    "none": "none",
    "nenhum": "none",
}

# Fallback para Windows sem o pacote tzdata (zoneinfo depende do banco do sistema).
BR_TZ_OFFSETS = {
    "america/sao_paulo": -3,
    "america/belem": -3,
    "america/fortaleza": -3,
    "america/recife": -3,
    "america/maceio": -3,
    "america/bahia": -3,
    "america/araguaina": -3,
    "america/manaus": -4,
    "america/cuiaba": -4,
    "america/porto_velho": -4,
    "america/rio_branco": -5,
    "america/noronha": -2,
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceFilter:
    """Filtros aplicados a uma fonte específica."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    breaking_prefixes: tuple[str, ...] = ()
    require_breaking: bool = False


class FeedError(Exception):
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TelegramError(Exception):
    pass


@dataclass
class Config:
    token: str
    chat_id: str
    thread_id: str
    sources: list[Source]
    poll_seconds: int
    send_delay: float
    single_max: int
    max_items_cycle: int
    max_messages_cycle: int
    fetch_timeout: int
    include_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    send_backlog: bool
    backlog_limit: int
    include_summary: bool
    calendar_enabled: bool
    calendar_at: str
    calendar_impacts: tuple[str, ...]
    calendar_countries: tuple[str, ...]
    calendar_source: str
    source_filters: dict[str, SourceFilter]
    disable_notification: bool
    disable_preview: bool
    preview_large: bool
    preview_digest: bool
    translate_enabled: bool
    translate_target: str
    translate_max: int
    resolve_links: bool
    resolve_max: int
    state_file: Path
    log_file: Path
    timezone_mode: str
    user_agent: str
    display_tz: tzinfo | None
    dry_run: bool = False


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser um número inteiro (valor atual: {raw!r}).") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} deve ser maior ou igual a {minimum} (valor atual: {value}).")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser um número (valor atual: {raw!r}).") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} deve ser maior ou igual a {minimum} (valor atual: {value}).")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    normalized = raw.casefold()
    if normalized in {"1", "true", "sim", "yes", "on", "y"}:
        return True
    if normalized in {"0", "false", "nao", "não", "no", "off", "n"}:
        return False
    raise ConfigError(f"{name} deve ser true/false (valor atual: {raw!r}).")


def _env_path(name: str, default: Path) -> Path:
    raw = _env(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = APP_DIR / path
    return path


def _fold(text: str) -> str:
    """Minúsculas e sem acentos, para comparação de palavras-chave."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _parse_keywords(name: str) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return ()
    keywords: list[str] = []
    for part in re.split(r"[,;\n]+", raw):
        keyword = _fold(part.strip())
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return tuple(keywords)


def _parse_sources() -> list[Source]:
    raw = _env("SOURCES", "fj,fxstreet")
    keys: list[str] = []
    for part in re.split(r"[,;\s]+", raw):
        key = part.strip().casefold()
        if key and key not in keys:
            keys.append(key)
    if not keys:
        raise ConfigError("SOURCES está vazio. Use fj, fxstreet ou fj,fxstreet.")
    unknown = [key for key in keys if key not in SOURCES]
    if unknown:
        valid = ", ".join(SOURCES)
        raise ConfigError(f"Fonte desconhecida em SOURCES: {', '.join(unknown)}. Válidas: {valid}.")
    return [SOURCES[key] for key in keys]


def _parse_calendar_at() -> str:
    raw = _env("CALENDAR_AT", "07:00") or "07:00"
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw.strip())
    if not match:
        raise ConfigError(f"CALENDAR_AT deve estar no formato HH:MM (valor atual: {raw!r}).")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _parse_calendar_impacts() -> tuple[str, ...]:
    raw = _env("CALENDAR_IMPACTS", "High") or "High"
    impacts: list[str] = []
    for part in re.split(r"[,;\s]+", raw):
        key = _fold(part)
        if not key:
            continue
        mapped = IMPACT_ALIASES.get(key)
        if mapped is None:
            valid = "high, medium, low, none"
            raise ConfigError(
                f"CALENDAR_IMPACTS: impacto desconhecido {part!r}. Use: {valid}."
            )
        if mapped not in impacts:
            impacts.append(mapped)
    if not impacts:
        raise ConfigError("CALENDAR_IMPACTS não pode ficar vazio.")
    return tuple(impacts)


def _parse_calendar_countries() -> tuple[str, ...]:
    raw = _env("CALENDAR_COUNTRIES", "")
    countries: list[str] = []
    for part in re.split(r"[,;\s]+", raw):
        key = _country_key(part)
        if key and key not in countries:
            countries.append(key)
    return tuple(countries)


def _parse_calendar_source() -> str:
    raw = _env("CALENDAR_SOURCE", "forexfactory").casefold()
    aliases = {
        "forexfactory": "forexfactory",
        "forex factory": "forexfactory",
        "ff": "forexfactory",
        "tradingview": "tradingview",
        "tv": "tradingview",
    }
    if raw not in aliases:
        raise ConfigError(
            f"CALENDAR_SOURCE inválido: {raw!r}. Use forexfactory ou tradingview."
        )
    return aliases[raw]


def _build_source_filters(
    global_include: tuple[str, ...], global_exclude: tuple[str, ...]
) -> dict[str, SourceFilter]:
    """Monta os filtros por fonte (FJ por categoria, FXStreet por importância)."""
    fj_cats_raw = _env("FJ_CATEGORIES", "forex,index,macro")
    fj_cats = tuple(
        c.strip().casefold()
        for c in re.split(r"[,;\s]+", fj_cats_raw)
        if c.strip()
    )
    fj_include_custom = _parse_keywords("FJ_INCLUDE_KEYWORDS")
    fj_exclude = _parse_keywords("FJ_EXCLUDE_KEYWORDS") or global_exclude

    if "all" in fj_cats or "tudo" in fj_cats:
        fj_include = fj_include_custom or global_include
    else:
        cat_words: list[str] = []
        for cat in fj_cats:
            if cat in ("forex", "fx", "cambio", "câmbio"):
                cat_words.extend(FJ_FOREX_KEYWORDS)
            elif cat in ("index", "indices", "índices", "indice", "índice", "acoes", "ações"):
                cat_words.extend(FJ_INDEX_KEYWORDS)
            elif cat in ("macro", "macroeconomia", "economia"):
                cat_words.extend(FJ_MACRO_KEYWORDS)
            else:
                raise ConfigError(
                    f"FJ_CATEGORIES contém categoria desconhecida: {cat!r}. "
                    "Use forex, index, macro ou all."
                )
        combined = list(dict.fromkeys(cat_words))
        if fj_include_custom:
            for w in fj_include_custom:
                if w not in combined:
                    combined.append(w)
        fj_include = tuple(combined)

    fj = SourceFilter(include=fj_include, exclude=fj_exclude)

    fx_include = _parse_keywords("FXSTREET_INCLUDE_KEYWORDS")
    fx_exclude = _parse_keywords("FXSTREET_EXCLUDE_KEYWORDS") or global_exclude
    mode = _env("FXSTREET_IMPORTANCE", "important").casefold()
    prefixes = FXSTREET_BREAKING_PREFIXES

    if mode == "all":
        fx = SourceFilter(include=fx_include or global_include, exclude=fx_exclude)
    elif mode == "breaking":
        fx = SourceFilter(
            include=(),
            exclude=fx_exclude,
            breaking_prefixes=prefixes,
            require_breaking=True,
        )
    elif mode in {"important", "importante"}:
        if not fx_include:
            fx_include = tuple(_fold(word) for word in FXSTREET_IMPORTANT_KEYWORDS)
        fx = SourceFilter(include=fx_include, exclude=fx_exclude, breaking_prefixes=prefixes)
    else:
        raise ConfigError(
            f"FXSTREET_IMPORTANCE inválido: {mode!r}. Use all, important ou breaking."
        )

    filters = {"fj": fj, "fxstreet": fx}

    # Reuters (via Google News): filtra macro/mercado para não entrar esporte.
    rt_include = _parse_keywords("REUTERS_INCLUDE_KEYWORDS")
    rt_exclude = _parse_keywords("REUTERS_EXCLUDE_KEYWORDS") or global_exclude
    rt_mode = _env("REUTERS_IMPORTANCE", "important").casefold()
    if rt_mode == "all":
        reuters = SourceFilter(include=rt_include or global_include, exclude=rt_exclude)
    elif rt_mode in {"important", "importante"}:
        if not rt_include:
            rt_include = tuple(_fold(word) for word in REUTERS_MARKET_KEYWORDS)
        reuters = SourceFilter(include=rt_include, exclude=rt_exclude)
    else:
        raise ConfigError(
            f"REUTERS_IMPORTANCE inválido: {rt_mode!r}. Use all ou important."
        )
    filters["reuters"] = reuters

    # Fontes extras (Investing etc.) usam os filtros globais por padrão.
    for key in SOURCES:
        filters.setdefault(key, SourceFilter(include=global_include, exclude=global_exclude))
    return filters


def _parse_fixed_offset(mode: str) -> timezone | None:
    match = re.fullmatch(r"(?:UTC)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", mode, re.IGNORECASE)
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    if hours > 14 or minutes > 59:
        return None
    return timezone(sign * (hours * 60 + minutes) * 60)


def _build_display_tz(mode: str) -> tzinfo | None:
    """Retorna None para o fuso local do sistema."""
    mode = (mode or "local").strip()
    if not mode or mode.casefold() == "local":
        return None
    if mode.casefold() == "utc":
        return timezone.utc
    fixed = _parse_fixed_offset(mode)
    if fixed is not None:
        return fixed
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(mode)
    except (KeyError, ValueError, OSError):
        offset = BR_TZ_OFFSETS.get(mode.casefold())
        if offset is not None:
            LOG.warning(
                "TIMEZONE=%s não disponível neste sistema; usando offset UTC%+d.",
                mode,
                offset,
            )
            return timezone(timedelta(hours=offset))
        LOG.warning("TIMEZONE=%s não reconhecido; usando o fuso local do sistema.", mode)
        return None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"Não foi possível ler {path}: {exc}") from exc

    for line_number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.casefold().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            LOG.warning("Linha %d do .env ignorada (nome inválido).", line_number)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        # Variáveis já definidas no ambiente têm prioridade sobre o .env.
        os.environ.setdefault(key, value)


def load_config(require_telegram: bool) -> Config:
    load_dotenv(APP_DIR / ".env")

    token = _env("TELEGRAM_BOT_TOKEN")
    chat_id = _env("TELEGRAM_CHAT_ID")
    # ID do tópico (topícios do grupo). Vazio = enviar na conversa principal.
    thread_id = _env("TELEGRAM_THREAD_ID")
    if thread_id and not re.fullmatch(r"-?\d+", thread_id):
        raise ConfigError(
            f"TELEGRAM_THREAD_ID deve ser um número (valor atual: {thread_id!r})."
        )
    if require_telegram:
        missing = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise ConfigError(
                "Preencha no arquivo .env: " + ", ".join(missing) + ".\n"
                "Veja o README.md para obter o token e o chat_id."
            )

    poll_seconds = _env_int("POLL_SECONDS", 120, minimum=30)
    send_delay = _env_float("SEND_DELAY_SECONDS", 3.0, minimum=0.5)
    single_max = _env_int("SINGLE_MAX", 4, minimum=1)
    max_items_cycle = _env_int("MAX_ITEMS_PER_CYCLE", 40, minimum=1)
    max_messages_cycle = _env_int("MAX_MESSAGES_PER_CYCLE", 12, minimum=1)
    fetch_timeout = _env_int("FETCH_TIMEOUT", 30, minimum=5)
    backlog_limit = min(_env_int("BACKLOG_LIMIT", 5, minimum=1), 50)

    timezone_mode = _env("TIMEZONE", "local") or "local"
    global_include = _parse_keywords("INCLUDE_KEYWORDS")
    global_exclude = _parse_keywords("EXCLUDE_KEYWORDS")

    cfg = Config(
        token=token,
        chat_id=chat_id,
        thread_id=thread_id,
        sources=_parse_sources(),
        poll_seconds=poll_seconds,
        send_delay=send_delay,
        single_max=single_max,
        max_items_cycle=max_items_cycle,
        max_messages_cycle=max_messages_cycle,
        fetch_timeout=fetch_timeout,
        include_keywords=global_include,
        exclude_keywords=global_exclude,
        send_backlog=_env_bool("SEND_BACKLOG", False),
        backlog_limit=backlog_limit,
        include_summary=_env_bool("INCLUDE_SUMMARY", True),
        calendar_enabled=_env_bool("CALENDAR_ENABLED", True),
        calendar_at=_parse_calendar_at(),
        calendar_impacts=_parse_calendar_impacts(),
        calendar_countries=_parse_calendar_countries(),
        calendar_source=_parse_calendar_source(),
        source_filters=_build_source_filters(global_include, global_exclude),
        disable_notification=_env_bool("DISABLE_NOTIFICATION", False),
        disable_preview=_env_bool("DISABLE_PREVIEW", True),
        preview_large=_env_bool("PREVIEW_LARGE", True),
        preview_digest=_env_bool("PREVIEW_DIGEST", False),
        translate_enabled=_env_bool("TRANSLATE_ENABLED", False),
        translate_target=_env("TRANSLATE_TARGET", "pt-BR") or "pt-BR",
        translate_max=_env_int("TRANSLATE_MAX_PER_CYCLE", 20, minimum=0),
        resolve_links=_env_bool("RESOLVE_LINKS", True),
        resolve_max=_env_int("RESOLVE_LINKS_MAX_PER_CYCLE", 10, minimum=0),
        state_file=_env_path("STATE_FILE", APP_DIR / "state.json"),
        log_file=_env_path("LOG_FILE", APP_DIR / "bot.log"),
        timezone_mode=timezone_mode,
        user_agent=_env("USER_AGENT", DEFAULT_UA) or DEFAULT_UA,
        display_tz=_build_display_tz(timezone_mode),
    )
    return cfg


# ---------------------------------------------------------------------------
# Estado (itens já vistos e cache HTTP)
# ---------------------------------------------------------------------------

class State:
    def __init__(self, path: Path, max_ids: int = 4000):
        self.path = path
        self.max_ids = max_ids
        self.sources: dict[str, dict] = {}
        self.calendar_sent = ""
        self._sets: dict[str, set[str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOG.warning("Estado ilegível em %s (%s); começando vazio.", self.path, exc)
            return

        if not isinstance(raw, dict):
            LOG.warning("Formato de estado inválido em %s; começando vazio.", self.path)
            return
        self.calendar_sent = str(raw.get("calendar_sent") or "")

        sources = raw.get("sources", {})
        if not isinstance(sources, dict):
            LOG.warning("Formato de estado inválido em %s; começando vazio.", self.path)
            return

        for key, entry in sources.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            seen_raw = entry.get("seen", [])
            if not isinstance(seen_raw, list):
                seen_raw = []
            seen: list[str] = []
            seen_set: set[str] = set()
            for value in seen_raw:
                if not isinstance(value, (str, int)):
                    continue
                item_id = str(value)
                if item_id not in seen_set:
                    seen.append(item_id)
                    seen_set.add(item_id)
            seen = seen[-self.max_ids :]
            seen_set = set(seen)
            self.sources[key] = {
                "seen": seen,
                "etag": str(entry.get("etag") or ""),
                "last_modified": str(entry.get("last_modified") or ""),
            }
            self._sets[key] = seen_set

    def has_source(self, key: str) -> bool:
        return key in self.sources

    def is_seen(self, key: str, item_id: str) -> bool:
        return item_id in self._sets.get(key, ())

    def mark_many(self, key: str, item_ids: Iterable[str]) -> None:
        entry = self.sources.get(key)
        if entry is None:
            entry = {"seen": [], "etag": "", "last_modified": ""}
            self.sources[key] = entry
            self._sets[key] = set()

        seen: list[str] = entry["seen"]
        seen_set = self._sets[key]
        for item_id in item_ids:
            if item_id in seen_set:
                continue
            seen.append(item_id)
            seen_set.add(item_id)

        if len(seen) > self.max_ids:
            removed = seen[: -self.max_ids]
            del seen[: -self.max_ids]
            seen_set.difference_update(removed)

    def cache_headers(self, key: str) -> dict[str, str]:
        entry = self.sources.get(key) or {}
        headers: dict[str, str] = {}
        etag = entry.get("etag")
        last_modified = entry.get("last_modified")
        if etag:
            headers["If-None-Match"] = str(etag)
        if last_modified:
            headers["If-Modified-Since"] = str(last_modified)
        return headers

    def update_cache(self, key: str, etag: str | None, last_modified: str | None) -> None:
        entry = self.sources.get(key)
        if entry is None:
            entry = {"seen": [], "etag": "", "last_modified": ""}
            self.sources[key] = entry
            self._sets[key] = set()
        if etag:
            entry["etag"] = etag
        if last_modified:
            entry["last_modified"] = last_modified

    def save(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "calendar_sent": self.calendar_sent,
            "sources": self.sources,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            LOG.error("Não foi possível salvar o estado em %s: %s", self.path, exc)


# ---------------------------------------------------------------------------
# Leitura dos feeds
# ---------------------------------------------------------------------------

@dataclass
class Item:
    source_key: str
    title: str
    link: str
    item_id: str
    published: datetime
    summary: str = ""


@dataclass
class FetchResult:
    source: Source
    items: list[Item]
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


def _clean_text(value: str, strip_tags: bool = False) -> str:
    value = html.unescape(value or "")
    if strip_tags:
        value = re.sub(r"<[^>]+>", " ", value)
    value = (
        value.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    return " ".join(value.split())


def _strip_tracking(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.casefold() != "xy"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        return url


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    parsed: datetime | None = None
    try:
        # RFC 822 (padrão dos RSS: "Sun, 08 Nov 2026 23:03:41 GMT").
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        # Alguns feeds (ex.: Investing.com) usam "2026-11-08 22:18:22".
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                # O fuso é aplicado logo abaixo (interpretação como UTC).
                parsed = datetime.strptime(value, fmt)  # noqa: DTZ007 - ver linha seguinte
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed(data: bytes, source: Source) -> list[Item]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")

    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as first_error:
        # Corrige eventuais "&" soltos e tenta novamente.
        fixed = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        try:
            root = ET.fromstring(fixed)
        except ET.ParseError as exc:
            raise FeedError(f"XML inválido: {first_error}") from exc

    channel = root.find("channel")
    container = channel if channel is not None else root
    nodes = container.findall("item")
    if not nodes:
        return []

    items: list[Item] = []
    seen_ids: set[str] = set()
    for node in nodes:
        title = _clean_text(node.findtext("title") or "")
        if not title:
            continue
        if source.key == "fj":
            title = re.sub(r"^FinancialJuice:\s*", "", title, flags=re.IGNORECASE)
        elif source.key == "reuters":
            # Google News acrescenta " - Reuters" ao título.
            title = re.sub(r"\s+-\s+Reuters\s*$", "", title, flags=re.IGNORECASE)

        link = (node.findtext("link") or "").strip()
        if not link:
            link_node = node.find("link")
            if link_node is not None:
                link = (link_node.get("href") or "").strip()
        if not link:
            continue
        link = _strip_tracking(link)

        guid = (node.findtext("guid") or "").strip()
        item_id = guid or link
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        published = _parse_date(node.findtext("pubDate"))
        if published is None:
            published = _parse_date(
                node.findtext("{http://purl.org/dc/elements/1.1/}date")
            )
        if published is None:
            published = datetime.now(timezone.utc)

        summary = _clean_text(node.findtext("description") or "", strip_tags=True)
        if summary.casefold() == title.casefold():
            summary = ""
        if source.key == "reuters":
            # O resumo do Google News é só o próprio link repetido.
            summary = ""

        items.append(
            Item(
                source_key=source.key,
                title=title,
                link=link,
                item_id=item_id,
                published=published,
                summary=summary,
            )
        )
    return items


def fetch_feed(source: Source, cfg: Config, cache_headers: dict[str, str] | None = None) -> FetchResult:
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if cache_headers:
        headers.update(cache_headers)

    request = urllib.request.Request(source.url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=cfg.fetch_timeout) as response:
            data = response.read()
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(source=source, items=[], not_modified=True)
        retry_after: int | None = None
        raw_retry = exc.headers.get("Retry-After") if exc.headers else None
        if raw_retry:
            try:
                retry_after = int(raw_retry)
            except ValueError:
                retry_after = None
        if exc.code == 429:
            detail = "muitas requisições (rate limit do provedor)"
        else:
            try:
                detail = " ".join(exc.read(300).decode("utf-8", errors="replace").split())
            except (OSError, ValueError):
                detail = ""
        message = f"HTTP {exc.code}"
        if retry_after:
            message += f" (Retry-After: {retry_after}s)"
        if detail:
            message += f" - {detail[:200]}"
        raise FeedError(message, retry_after=retry_after) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedError(f"falha de conexão: {exc}") from exc

    if not data:
        raise FeedError("resposta vazia")

    items = parse_feed(data, source)
    return FetchResult(
        source=source,
        items=items,
        etag=etag,
        last_modified=last_modified,
    )


# ---------------------------------------------------------------------------
# Filtros e formatação
# ---------------------------------------------------------------------------

def _match_keyword(word: str, text: str) -> bool:
    """Verifica correspondência de palavra-chave; termos curtos (<=4 caracteres) usam limite de palavra."""
    if len(word) <= 4:
        return bool(re.search(r"(?:\b|_)" + re.escape(word) + r"(?:\b|_)", text, re.IGNORECASE))
    return word in text


def matches_filters(item: Item, cfg: Config) -> bool:
    source_filter = cfg.source_filters.get(item.source_key, SourceFilter())
    folded_title = _fold(item.title)
    breaking = any(
        folded_title.startswith(prefix) for prefix in source_filter.breaking_prefixes
    )
    if source_filter.require_breaking and not breaking:
        return False

    haystack = f"{folded_title} {_fold(item.summary)}"
    if (
        source_filter.include
        and not breaking
        and not any(_match_keyword(word, haystack) for word in source_filter.include)
    ):
        return False
    return not (
        source_filter.exclude and any(_match_keyword(word, haystack) for word in source_filter.exclude)
    )


def filter_items(items: Iterable[Item], cfg: Config) -> list[Item]:
    return [item for item in items if matches_filters(item, cfg)]


def dedupe_items(items: Iterable[Item]) -> list[Item]:
    result: list[Item] = []
    seen: set[str] = set()
    for item in items:
        if item.item_id in seen:
            continue
        seen.add(item.item_id)
        result.append(item)
    result.sort(key=lambda item: item.published)
    return result


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _esc(text: str, quote: bool = False) -> str:
    return html.escape(text or "", quote=quote)


def _local_time(cfg: Config) -> datetime:
    if cfg.display_tz is not None:
        return datetime.now(cfg.display_tz)
    return datetime.now().astimezone()


def _item_time(item: Item, cfg: Config) -> str:
    if cfg.display_tz is not None:
        local = item.published.astimezone(cfg.display_tz)
    else:
        local = item.published.astimezone()
    now = _local_time(cfg)
    if local.date() != now.date():
        return local.strftime("%d/%m %H:%M")
    return local.strftime("%H:%M")


def _time_range(items: list[Item], cfg: Config) -> str:
    if not items:
        return ""
    first = _item_time(items[0], cfg)
    last = _item_time(items[-1], cfg)
    return first if first == last else f"{first}–{last}"


def format_item(item: Item, cfg: Config, translator: Translator | None = None) -> str:
    source = SOURCES[item.source_key]
    title_text = item.title
    summary_text = item.summary
    if translator is not None:
        title_text = translator.translate(title_text)
        if summary_text:
            summary_text = translator.translate(summary_text)
    title = _truncate(title_text, 600)
    link = _esc(_item_link(item, cfg), quote=True)
    lines = [
        f"{source.emoji} <b>{_esc(source.name)}</b> · <i>{_item_time(item, cfg)}</i>",
        f'<a href="{link}">{_esc(title)}</a>',
    ]
    if cfg.include_summary and summary_text:
        summary = _truncate(summary_text, 450)
        lines.append(f"<i>{_esc(summary)}</i>")
    return "\n".join(lines)


def format_digest(
    source: Source,
    items: list[Item],
    cfg: Config,
    translator: Translator | None = None,
) -> list[tuple[str, list[Item]]]:
    base = (
        f"{source.emoji} <b>{_esc(source.name)}</b> · "
        f"<b>{len(items)}</b> novas · <i>{_time_range(items, cfg)}</i>"
    )
    lines: list[tuple[str, Item]] = []
    for item in items:
        title = item.title
        if translator is not None:
            title = translator.translate(title)
        title = _truncate(title, 500)
        line = f'• <a href="{_esc(_item_link(item, cfg), quote=True)}">{_esc(title)}</a>'
        lines.append((line, item))

    chunks: list[list[tuple[str, Item]]] = []
    current: list[tuple[str, Item]] = []
    current_len = 0
    budget = MAX_TELEGRAM_TEXT - len(base) - 40
    for line, item in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > budget:
            chunks.append(current)
            current = []
            current_len = 0
        current.append((line, item))
        current_len += line_len
    if current:
        chunks.append(current)

    messages: list[tuple[str, list[Item]]] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, 1):
        header = base
        if total > 1:
            header += f" <i>({index}/{total})</i>"
        text = header + "\n\n" + "\n".join(line for line, _ in chunk)
        messages.append((text, [item for _, item in chunk]))
    return messages


def html_to_plain(text: str) -> str:
    def anchor(match: re.Match[str]) -> str:
        url = html.unescape(match.group(1))
        label = html.unescape(match.group(2))
        return f"{label} ({url})"

    text = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', anchor, text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# ---------------------------------------------------------------------------
# Tradução das manchetes (inglês -> português)
# ---------------------------------------------------------------------------

TRANSLATE_URL_GOOGLE = "https://translate.googleapis.com/translate_a/single"
TRANSLATE_URL_MYMEMORY = "https://api.mymemory.translated.net/get"
TRANSLATE_CACHE_VERSION = 1


class Translator:
    """Traduz manchetes/resumos com cache em disco e reserva entre provedores."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.target = (cfg.translate_target or "pt-BR").strip()
        self.cache_path = CACHE_DIR / "translations.json"
        self.cache: dict[str, str] = {}
        self.used = 0
        self._cooldown_until = 0.0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("map") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self.cache = {str(k): str(v) for k, v in entries.items() if isinstance(v, str)}

    def _save(self) -> None:
        if len(self.cache) > 5000:
            for key in list(self.cache)[: len(self.cache) - 5000]:
                del self.cache[key]
        payload = {"version": TRANSLATE_CACHE_VERSION, "map": self.cache}
        tmp = self.cache_path.with_suffix(".tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            LOG.debug("Não consegui gravar o cache de traduções: %s", exc)

    def reset_cycle(self) -> None:
        self.used = 0

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.target}\x00{text}".encode("utf-8")).hexdigest()

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return text
        cached = self.cache.get(self._key(text))
        if cached is not None:
            return cached
        if self.used >= self.cfg.translate_max:
            return text

        self.used += 1
        translated = self._google(text)
        if translated is None:
            translated = self._mymemory(text)
        if not translated:
            return text

        translated = translated.strip() or text
        self.cache[self._key(text)] = translated
        self._save()
        return translated

    def _get(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self.cfg.user_agent, "Accept": "application/json, */*"},
        )
        with urllib.request.urlopen(request, timeout=min(self.cfg.fetch_timeout, 10)) as response:
            return response.read()

    def _google(self, text: str) -> str | None:
        if time.monotonic() < self._cooldown_until:
            return None
        base = self.target.split("-")[0]
        query = urlencode({"client": "gtx", "sl": "auto", "tl": base, "dt": "t", "q": text})
        try:
            raw = self._get(f"{TRANSLATE_URL_GOOGLE}?{query}")
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            LOG.debug("Google Translate indisponível: %s", exc)
            self._cooldown_until = time.monotonic() + 300
            return None

        try:
            translated = "".join(part[0] for part in data[0] if part and part[0])
            detected = str(data[2] or "").split("-")[0].casefold()
        except (TypeError, IndexError, KeyError):
            return None
        if not translated:
            return None
        # Já está no idioma de destino (ex.: feeds do Investing Brasil).
        if detected == base:
            return text
        return translated

    def _mymemory(self, text: str) -> str | None:
        if len(text) > 500:
            return None
        query = urlencode({"q": text, "langpair": f"en|{self.target}"})
        try:
            raw = self._get(f"{TRANSLATE_URL_MYMEMORY}?{query}")
            data = json.loads(raw.decode("utf-8", errors="replace"))
            translated = str((data.get("responseData") or {}).get("translatedText") or "")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError):
            return None
        return translated or None


_TRANSLATOR: Translator | None = None


def get_translator(cfg: Config) -> Translator | None:
    """Tradutor compartilhado (singleton), criado só se TRANSLATE_ENABLED=true."""
    global _TRANSLATOR
    if not cfg.translate_enabled:
        return None
    if _TRANSLATOR is None:
        _TRANSLATOR = Translator(cfg)
    return _TRANSLATOR


# ---------------------------------------------------------------------------
# Links do Google News -> URL original da Reuters
# ---------------------------------------------------------------------------

GNEWS_DECODE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
GNEWS_LINK_CACHE_VERSION = 1


class GoogleNewsResolver:
    """Converte os links do Google News na URL real do veículo (com cache).

    O link do Google não redireciona via HTTP (é JavaScript), então o preview
    do Telegram mostraria só o Google. Aqui decodificamos a URL de verdade,
    o que também deixa o link da mensagem limpo (reuters.com/...).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache_path = CACHE_DIR / "gnews_links.json"
        self.cache: dict[str, str] = {}
        self.used = 0
        self._cooldown_until = 0.0
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("map") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self.cache = {str(k): str(v) for k, v in entries.items() if isinstance(v, str)}

    def _save(self) -> None:
        if len(self.cache) > 5000:
            for key in list(self.cache)[: len(self.cache) - 5000]:
                del self.cache[key]
        payload = {"version": GNEWS_LINK_CACHE_VERSION, "map": self.cache}
        tmp = self.cache_path.with_suffix(".tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            LOG.debug("Não consegui gravar o cache de links: %s", exc)

    def reset_cycle(self) -> None:
        self.used = 0

    def resolve(self, link: str) -> str:
        if "news.google.com" not in link or "/articles/" not in link:
            return link
        cached = self.cache.get(link)
        if cached:
            return cached
        if self.used >= self.cfg.resolve_max or time.monotonic() < self._cooldown_until:
            return link

        self.used += 1
        try:
            final = self._decode(link)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            LOG.debug("Não consegui decodificar o link do Google News: %s", exc)
            self._cooldown_until = time.monotonic() + 120
            return link

        if not final:
            return link
        self.cache[link] = final
        self._save()
        return final

    def _decode(self, link: str) -> str | None:
        article_id = link.split("/articles/", 1)[1].split("?", 1)[0]
        timeout = min(self.cfg.fetch_timeout, 8)
        headers = {
            "User-Agent": self.cfg.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }

        request = urllib.request.Request(link, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", errors="replace")

        signature = re.search(r'data-n-a-sg="([^"]+)"', page)
        timestamp = re.search(r'data-n-a-ts="(\d+)"', page)
        if not (signature and timestamp):
            return None

        inner = json.dumps(
            [
                "garturlreq",
                [
                    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                     None, None, None, None, None, 0, 1],
                    "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
                ],
                article_id,
                int(timestamp.group(1)),
                signature.group(1),
            ]
        )
        body = urlencode(
            {"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}
        ).encode("utf-8")

        decode_request = urllib.request.Request(
            GNEWS_DECODE_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": self.cfg.user_agent,
            },
        )
        with urllib.request.urlopen(decode_request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")

        # A resposta vem com as aspas escapadas (\"), então normalizamos antes.
        normalized = raw.replace('\\"', '"')
        match = re.search(r'"garturlres","(https?:[^"]+)"', normalized)
        if not match:
            return None
        return match.group(1).replace("\\/", "/")


_RESOLVER: GoogleNewsResolver | None = None


def get_resolver(cfg: Config) -> GoogleNewsResolver | None:
    global _RESOLVER
    if not cfg.resolve_links:
        return None
    if _RESOLVER is None:
        _RESOLVER = GoogleNewsResolver(cfg)
    return _RESOLVER


def _item_link(item: Item, cfg: Config) -> str:
    resolver = get_resolver(cfg)
    return resolver.resolve(item.link) if resolver is not None else item.link


# ---------------------------------------------------------------------------
# Calendário econômico (Forex Factory, feed ICS público)
# ---------------------------------------------------------------------------

CALENDAR_URL_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.ics"
CALENDAR_URL_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.ics"
# Reserva: calendário público do TradingView (JSON, horários em UTC).
TRADINGVIEW_CALENDAR_URL = "https://economic-calendar.tradingview.com/events"
TRADINGVIEW_CALENDAR_HEADERS = {
    "Referer": "https://www.tradingview.com/",
    "Origin": "https://www.tradingview.com",
    "Accept": "application/json, text/plain, */*",
}
# Códigos ISO (TradingView) -> códigos do ForexFactory, para comparar os filtros.
ISO_TO_FF_COUNTRY = {"GB": "UK", "JP": "JN", "DE": "GE", "EU": "EZ", "CN": "CH"}
# O Forex Factory responde 429 se pedirmos o arquivo seguido; cacheamos por 6 h.
CALENDAR_CACHE_TTL = 6 * 3600

WEEKDAY_PT = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
IMPACT_EMOJI = {"high": "🔴", "medium": "🟡", "low": "⚪", "none": "⚪"}
IMPACT_LABEL_PT = {"high": "alto", "medium": "médio", "low": "baixo", "none": "nenhum"}


@dataclass
class CalendarEvent:
    uid: str
    start: datetime
    country: str
    title: str
    impact: str
    forecast: str
    previous: str
    url: str


def _fetch_text(url: str, cfg: Config, headers: dict[str, str] | None = None) -> str:
    request_headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "text/calendar, text/plain, application/calendar;q=0.9, */*;q=0.5",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=cfg.fetch_timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After", "").strip() if exc.headers else ""
        hint = f" (Retry-After: {retry_after}s)" if retry_after else ""
        raise FeedError(f"HTTP {exc.code} ao buscar {url}{hint}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedError(f"falha de conexão ao buscar {url}: {exc}") from exc

    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fetch_text_cached(
    url: str,
    cfg: Config,
    ttl: int,
    headers: dict[str, str] | None = None,
    ext: str = ".ics",
) -> str:
    """Como _fetch_text, mas guarda uma cópia em disco (o Forex Factory limita os acessos)."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{digest}{ext}"

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime <= ttl:
                return cache_file.read_text(encoding="utf-8")
        except OSError:
            pass

    try:
        text = _fetch_text(url, cfg, headers=headers)
    except FeedError as exc:
        # Cache velho ainda é melhor do que nada (evita falhar o briefing).
        if cache_file.exists():
            try:
                age = time.time() - cache_file.stat().st_mtime
                if age <= 48 * 3600:
                    LOG.warning("Calendário: %s; usando a cópia em cache.", exc)
                    return cache_file.read_text(encoding="utf-8")
            except OSError:
                pass
        raise

    try:
        cache_file.write_text(text, encoding="utf-8")
    except OSError as exc:
        LOG.debug("Não consegui gravar o cache do calendário: %s", exc)
    return text


def _ics_unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_unescape(value: str) -> str:
    value = value.replace("\\n", "\n").replace("\\N", "\n")
    value = value.replace("\\,", ",").replace("\\;", ";")
    return value.replace("\\\\", "\\")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _tz_from_name(tzid: str | None) -> tzinfo:
    if tzid:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(tzid)
        except (KeyError, ValueError, OSError):
            pass
        if tzid.casefold() == "america/new_york":
            # Fallback sem tzdata: horário de verão dos EUA.
            now = datetime.now(timezone.utc)
            dst_start = _nth_weekday(now.year, 3, 6, 2)
            dst_end = _nth_weekday(now.year, 11, 6, 1)
            if dst_start.date() <= now.date() < dst_end.date():
                return timezone(timedelta(hours=-4))
            return timezone(timedelta(hours=-5))
        LOG.warning("Fuso %s desconhecido; interpretando como UTC.", tzid)
    return timezone.utc


def _parse_ics_datetime(value: str, tzid: str | None) -> datetime | None:
    value = value.strip()
    try:
        if value.endswith(("Z", "z")):
            return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=_tz_from_name(tzid))
    except ValueError:
        return None


def _ics_property(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for param in parts[1:]:
        if "=" in param:
            key, _, val = param.partition("=")
            params[key.strip().upper()] = val.strip()
    return name, params, value


def parse_calendar(text: str) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    props: dict[str, tuple[dict[str, str], str]] | None = None

    for line in _ics_unfold(text):
        if line.strip() == "BEGIN:VEVENT":
            props = {}
            continue
        if line.strip() == "END:VEVENT":
            if props is not None:
                event = _build_calendar_event(props)
                if event is not None:
                    events.append(event)
            props = None
            continue
        if props is None or ":" not in line:
            continue
        name, params, value = _ics_property(line)
        if name:
            props[name] = (params, value)

    events.sort(key=lambda event: event.start)
    return events


def _build_calendar_event(props: dict[str, tuple[dict[str, str], str]]) -> CalendarEvent | None:
    dtstart = props.get("DTSTART")
    if not dtstart:
        return None
    params, value = dtstart
    start = _parse_ics_datetime(value, params.get("TZID"))
    if start is None:
        return None

    summary = _ics_unescape(props.get("SUMMARY", ("", ""))[1]).strip()
    description = _ics_unescape(props.get("DESCRIPTION", ("", ""))[1])
    url = props.get("URL", ("", ""))[1].strip()
    uid = props.get("UID", ("", ""))[1].strip()

    impact_match = re.search(r"Impact:\s*([A-Za-z]+)", description, re.IGNORECASE)
    forecast_match = re.search(r"Forecast:\s*(.*?)(?:\n|$)", description, re.IGNORECASE)
    previous_match = re.search(r"Previous:\s*(.*?)(?:\n|$)", description, re.IGNORECASE)

    # SUMMARY: "USD Non-Farm Payrolls" (às vezes com símbolo no início).
    plain = re.sub(r"^[^A-Za-z0-9]+", "", summary).strip()
    pieces = plain.split(None, 1)
    country = ""
    title = plain
    if pieces and re.fullmatch(r"[A-Za-z]{2,3}", pieces[0]):
        country = pieces[0].upper()
        title = pieces[1].strip() if len(pieces) > 1 else ""

    if not title:
        return None

    return CalendarEvent(
        uid=uid,
        start=start,
        country=country,
        title=title,
        impact=(impact_match.group(1) if impact_match else "None").capitalize(),
        forecast=(forecast_match.group(1).strip() if forecast_match else ""),
        previous=(previous_match.group(1).strip() if previous_match else ""),
        url=url,
    )


def _country_key(raw: str) -> str:
    """Normaliza o código do país (ISO ou ForexFactory) para o padrão do ForexFactory."""
    code = (raw or "").strip().upper()
    return ISO_TO_FF_COUNTRY.get(code, code).casefold()


def _fetch_calendar_ff(cfg: Config, wanted: date) -> list[CalendarEvent]:
    """Agenda do Forex Factory (ICS). É cacheada porque eles limitam os acessos."""
    events = parse_calendar(_fetch_text_cached(CALENDAR_URL_THISWEEK, cfg, CALENDAR_CACHE_TTL))
    if events:
        dates = [event.start.astimezone(_display_tz(cfg)).date() for event in events]
        if wanted > max(dates):
            try:
                events.extend(
                    parse_calendar(
                        _fetch_text_cached(CALENDAR_URL_NEXTWEEK, cfg, CALENDAR_CACHE_TTL)
                    )
                )
            except FeedError as exc:
                LOG.debug("Agenda da próxima semana indisponível: %s", exc)
    return events


def _parse_tv_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)  # noqa: DTZ007 - vira UTC logo abaixo
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _tv_importance(value: object) -> str:
    try:
        level = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "None"
    return {1: "High", 0: "Medium", -1: "Low"}.get(level, "None")


def _tv_number(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def parse_calendar_tv(text: str) -> list[CalendarEvent]:
    """Converte a resposta JSON do TradingView em eventos de calendário."""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise FeedError(f"TradingView devolveu JSON inválido: {exc}") from exc

    rows = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FeedError("Resposta do TradingView sem a lista 'result'.")

    events: list[CalendarEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = _parse_tv_datetime(str(row.get("date") or ""))
        if start is None:
            continue
        title = _clean_text(str(row.get("title") or row.get("indicator") or ""))
        if not title:
            continue
        events.append(
            CalendarEvent(
                uid=str(row.get("id") or ""),
                start=start,
                country=str(row.get("country") or "").upper(),
                title=title,
                impact=_tv_importance(row.get("importance")),
                forecast=_tv_number(row.get("forecast")),
                previous=_tv_number(row.get("previous")),
                url=str(row.get("source_url") or ""),
            )
        )
    events.sort(key=lambda event: event.start)
    return events


def _fetch_calendar_tv(cfg: Config, wanted: date) -> list[CalendarEvent]:
    # Pede ±1 dia para cobrir a virada de data entre UTC e Brasília.
    d1 = (wanted - timedelta(days=1)).isoformat()
    d2 = (wanted + timedelta(days=1)).isoformat()
    url = f"{TRADINGVIEW_CALENDAR_URL}?from={d1}&to={d2}&limit=500"
    text = _fetch_text_cached(
        url,
        cfg,
        CALENDAR_CACHE_TTL,
        headers=TRADINGVIEW_CALENDAR_HEADERS,
        ext=".json",
    )
    return parse_calendar_tv(text)


def fetch_calendar_events(cfg: Config, target: date | None = None) -> list[CalendarEvent]:
    """Busca a agenda: Forex Factory (padrão) com reserva automática no TradingView."""
    wanted = target or _local_time(cfg).date()
    if cfg.calendar_source == "tradingview":
        events = _fetch_calendar_tv(cfg, wanted)
    else:
        try:
            events = _fetch_calendar_ff(cfg, wanted)
        except FeedError as exc:
            LOG.warning("ForexFactory indisponível (%s); usando o TradingView como reserva.", exc)
            events = _fetch_calendar_tv(cfg, wanted)
    events.sort(key=lambda event: event.start)
    return events


def _display_tz(cfg: Config) -> tzinfo:
    if cfg.display_tz is not None:
        return cfg.display_tz
    return datetime.now().astimezone().tzinfo or timezone.utc


def filter_calendar_today(
    events: Iterable[CalendarEvent], cfg: Config, target: date | None = None
) -> list[tuple[datetime, CalendarEvent]]:
    tz = _display_tz(cfg)
    today = target or _local_time(cfg).date()
    selected: list[tuple[datetime, CalendarEvent]] = []
    for event in events:
        local = event.start.astimezone(tz)
        if local.date() != today:
            continue
        if cfg.calendar_impacts and event.impact.casefold() not in cfg.calendar_impacts:
            continue
        if cfg.calendar_countries and _country_key(event.country) not in cfg.calendar_countries:
            continue
        selected.append((local, event))
    selected.sort(key=lambda pair: pair[0])
    return selected


def format_calendar(
    selected: list[tuple[datetime, CalendarEvent]],
    cfg: Config,
    target: date,
) -> str | None:
    impacts = (
        ", ".join(IMPACT_LABEL_PT.get(impact, impact) for impact in sorted(cfg.calendar_impacts))
        if cfg.calendar_impacts
        else "todos"
    )
    zone_label = (
        cfg.timezone_mode
        if cfg.timezone_mode.casefold() != "local"
        else _local_time(cfg).strftime("%Z")
    )
    header = (
        f"📅 <b>Agenda econômica — {WEEKDAY_PT[target.weekday()]}, {target.strftime('%d/%m')}</b>\n"
        f"🕐 Horários em <i>{_esc(zone_label or 'local')}</i> · impacto: {_esc(impacts)}"
    )

    if not selected:
        if target.weekday() >= 5:
            return None  # fim de semana sem eventos: não polui o chat
        return header + "\n\nNenhum evento relevante programado para hoje. ✅"

    lines: list[str] = []
    for local, event in selected:
        emoji = IMPACT_EMOJI.get(event.impact.casefold(), "⚪")
        title = _truncate(event.title, 160)
        line = f"{emoji} <b>{local.strftime('%H:%M')}</b> · <b>{_esc(event.country or '—')}</b> — {_esc(title)}"
        details = []
        if event.forecast:
            details.append(f"Cons: {_esc(event.forecast)}")
        if event.previous:
            details.append(f"Ant: {_esc(event.previous)}")
        if details:
            line += "\n   <i>" + " · ".join(details) + "</i>"
        lines.append(line)

    messages: list[str] = []
    current = header
    for line in lines:
        if len(current) + len(line) + 2 > MAX_TELEGRAM_TEXT:
            messages.append(current)
            current = header + " <i>(continuação)</i>"
        current += "\n" + line
    if current != header:
        messages.append(current)
    return "\n\n".join(messages)


def build_calendar_message(cfg: Config, target: date | None = None) -> str | None:
    day = target or _local_time(cfg).date()
    events = fetch_calendar_events(cfg, day)
    selected = filter_calendar_today(events, cfg, day)
    return format_calendar(selected, cfg, day)


def maybe_send_calendar(
    cfg: Config, state: State, telegram: Telegram | None, dry_run: bool
) -> bool:
    """Envia a agenda do dia uma única vez, na hora configurada em CALENDAR_AT."""
    if not cfg.calendar_enabled:
        return False

    now = _local_time(cfg)
    hour, minute = (int(part) for part in cfg.calendar_at.split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False

    today = now.date()
    if state.calendar_sent == today.isoformat():
        return False

    try:
        message = build_calendar_message(cfg, today)
    except FeedError as exc:
        LOG.warning("Calendário: %s. Tentando de novo no próximo ciclo.", exc)
        return False

    if message is None:
        state.calendar_sent = today.isoformat()
        if not dry_run:
            state.save()
        LOG.info("Calendário: fim de semana sem eventos; nada a enviar.")
        return False

    if dry_run:
        _print_preview(message)
        state.calendar_sent = today.isoformat()
        return False

    if telegram is None:
        raise TelegramError("cliente do Telegram não inicializado")
    telegram.send(message)
    state.calendar_sent = today.isoformat()
    state.save()
    LOG.info("Calendário: agenda do dia enviada.")
    return True


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _api(self, method: str, payload: dict, timeout: int = 30) -> object:
        url = f"https://api.telegram.org/bot{self.cfg.token}/{method}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        last_error = "erro desconhecido"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise TelegramError("resposta inválida do Telegram")
                if not parsed.get("ok"):
                    raise TelegramError(str(parsed.get("description") or "resposta inválida do Telegram"))
                return parsed.get("result")
            except urllib.error.HTTPError as exc:
                description = ""
                retry_after: int | None = None
                try:
                    raw_error = exc.read().decode("utf-8", errors="replace")
                    parsed_error = json.loads(raw_error)
                    if isinstance(parsed_error, dict):
                        description = str(parsed_error.get("description") or "")
                        retry_after = (parsed_error.get("parameters") or {}).get("retry_after")
                except (OSError, ValueError, TypeError, AttributeError):
                    description = ""
                description = description or f"HTTP {exc.code}"

                if exc.code == 429 and attempt < 2:
                    wait = min(int(retry_after or 5), 90)
                    LOG.warning("Telegram: limite de requisições; aguardando %ds...", wait)
                    time.sleep(wait)
                    continue
                if exc.code >= 500 and attempt < 2:
                    LOG.warning("Telegram: erro %d; tentando novamente em 3s...", exc.code)
                    time.sleep(3)
                    continue
                raise TelegramError(description) from exc
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = f"falha de conexão: {exc}"
                if attempt < 2:
                    LOG.warning("Telegram: %s; tentando novamente em 3s...", exc)
                    time.sleep(3)
                    continue
                break
        raise TelegramError(last_error)

    def get_me(self) -> dict:
        result = self._api("getMe", {})
        if not isinstance(result, dict):
            raise TelegramError("getMe retornou formato inesperado")
        return result

    def get_chat(self) -> dict:
        result = self._api("getChat", {"chat_id": self.cfg.chat_id})
        if not isinstance(result, dict):
            raise TelegramError("getChat retornou formato inesperado")
        return result

    def list_chats(self) -> list[dict]:
        result = self._api("getUpdates", {"limit": 100, "timeout": 0})
        if not isinstance(result, list):
            return []
        chats: dict[int, dict] = {}
        for update in result:
            if not isinstance(update, dict):
                continue
            for key in ("message", "channel_post", "edited_message", "edited_channel_post", "my_chat_member"):
                payload = update.get(key)
                if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
                    chat = payload["chat"]
                    chat_id = chat.get("id")
                    if isinstance(chat_id, int):
                        chats.setdefault(chat_id, chat)
        return list(chats.values())

    def _base_payload(self, text: str, preview: bool | None = None) -> dict:
        if preview is None:
            preview = not self.cfg.disable_preview
        payload = {
            "chat_id": self.cfg.chat_id,
            "text": text,
            "disable_web_page_preview": not preview,
            "disable_notification": self.cfg.disable_notification,
        }
        if preview:
            payload["link_preview_options"] = {
                "is_disabled": False,
                "prefer_large_media": self.cfg.preview_large,
                "show_above_text": False,
            }
        if self.cfg.thread_id:
            payload["message_thread_id"] = int(self.cfg.thread_id)
        return payload

    def send(self, html_text: str, preview: bool | None = None) -> None:
        payload = self._base_payload(html_text, preview)
        payload["parse_mode"] = "HTML"
        try:
            self._api("sendMessage", payload)
            return
        except TelegramError as exc:
            description = str(exc).casefold()
            if "link_preview_options" in description and "link_preview_options" in payload:
                # API antiga: reenvia sem o card, mantendo o preview padrão.
                LOG.warning("Telegram recusou link_preview_options (%s); usando preview padrão.", exc)
                payload.pop("link_preview_options")
                self._api("sendMessage", payload)
                return
            html_problem = any(
                marker in description
                for marker in (
                    "parse entities",
                    "unsupported start tag",
                    "can't find end tag",
                    "unmatched end tag",
                    "entity",
                )
            )
            if not html_problem:
                raise
            LOG.warning("Telegram recusou o HTML (%s); reenviando como texto simples.", exc)

        plain = _truncate(html_to_plain(html_text), 3900)
        self._api("sendMessage", self._base_payload(plain, preview))


# ---------------------------------------------------------------------------
# Envio e ciclo principal
# ---------------------------------------------------------------------------

@dataclass
class Runtime:
    failures: dict[str, int] = field(default_factory=dict)
    next_try: dict[str, float] = field(default_factory=dict)


def _print_preview(text: str) -> None:
    line = "─" * 60
    print(line)
    print(html_to_plain(text))
    print(line)


def deliver_items(
    source: Source,
    items: list[Item],
    cfg: Config,
    state: State,
    telegram: Telegram | None,
    dry_run: bool,
) -> tuple[int, bool]:
    """Envia itens. Retorna (quantidade enviada, envio completo)."""
    if not items:
        return 0, True

    translator = get_translator(cfg)
    if translator is not None:
        translator.reset_cycle()
    resolver = get_resolver(cfg)
    if resolver is not None:
        resolver.reset_cycle()
    is_digest = len(items) > cfg.single_max
    show_preview = (not cfg.disable_preview) and (cfg.preview_digest or not is_digest)

    if not is_digest:
        messages = [(format_item(item, cfg, translator), [item]) for item in items]
    else:
        messages = format_digest(source, items, cfg, translator)

    sent_items = 0
    complete = True
    for index, (text, chunk_items) in enumerate(messages):
        if index >= cfg.max_messages_cycle:
            LOG.warning(
                "%s: limite de %d mensagens por ciclo atingido; %d item(ns) ficam para o próximo ciclo.",
                source.name,
                cfg.max_messages_cycle,
                sum(len(chunk) for _, chunk in messages[index:]),
            )
            complete = False
            break
        if dry_run:
            _print_preview(text)
        else:
            if telegram is None:
                raise TelegramError("cliente do Telegram não inicializado")
            telegram.send(text, preview=show_preview)
            state.mark_many(source.key, [item.item_id for item in chunk_items])
            state.save()
        sent_items += len(chunk_items)
        if index < len(messages) - 1:
            time.sleep(cfg.send_delay)
    return sent_items, complete


def process_source(
    source: Source,
    cfg: Config,
    state: State,
    telegram: Telegram | None,
    runtime: Runtime,
    dry_run: bool,
) -> int:
    now = time.monotonic()
    next_try = runtime.next_try.get(source.key, 0.0)
    if now < next_try:
        LOG.info("%s: em espera por %ds após falha.", source.name, int(next_try - now))
        return 0

    try:
        result = fetch_feed(source, cfg, state.cache_headers(source.key))
    except FeedError as exc:
        failures = runtime.failures.get(source.key, 0) + 1
        runtime.failures[source.key] = failures
        if exc.retry_after:
            # Limite de requisições: volta ao ritmo normal, sem crescer demais.
            delay = max(cfg.poll_seconds, min(exc.retry_after, 300))
        else:
            delay = min(max(cfg.poll_seconds, 30) * (2 ** (failures - 1)), 900)
        delay = int(min(delay, 1800))
        runtime.next_try[source.key] = time.monotonic() + delay
        LOG.warning("%s: %s. Próxima tentativa em %ds.", source.name, exc, delay)
        return 0

    runtime.failures.pop(source.key, None)
    runtime.next_try.pop(source.key, None)

    if result.not_modified:
        LOG.debug("%s: feed sem alterações (304).", source.name)
        return 0

    all_items = dedupe_items(result.items)
    if not all_items:
        LOG.info("%s: feed sem itens.", source.name)
        if not dry_run:
            state.update_cache(source.key, result.etag, result.last_modified)
            state.save()
        return 0

    first_run = not state.has_source(source.key)
    if first_run:
        backlog: list[Item] = []
        if cfg.send_backlog:
            backlog = filter_items(all_items, cfg)[-cfg.backlog_limit :]
        backlog_ids = {item.item_id for item in backlog}
        ignored = [item for item in all_items if item.item_id not in backlog_ids]

        LOG.info(
            "%s: primeira execução; %d item(ns) atual(is)%s.",
            source.name,
            len(all_items),
            f", enviando os {len(backlog)} mais recentes como backlog" if backlog else " (nada será enviado agora)",
        )
        if dry_run:
            if backlog:
                deliver_items(source, backlog, cfg, state, telegram, dry_run=True)
            return 0
        state.mark_many(source.key, (item.item_id for item in ignored))
        sent = 0
        complete = True
        if backlog:
            try:
                sent, complete = deliver_items(source, backlog, cfg, state, telegram, dry_run=False)
            except TelegramError as exc:
                LOG.error("%s: falha ao enviar backlog: %s", source.name, exc)
                state.save()
                return 0
        if complete:
            state.update_cache(source.key, result.etag, result.last_modified)
        state.save()
        return sent

    new_items = [item for item in all_items if not state.is_seen(source.key, item.item_id)]
    if not new_items:
        LOG.debug("%s: nenhuma notícia nova.", source.name)
        if not dry_run:
            state.update_cache(source.key, result.etag, result.last_modified)
            state.save()
        return 0

    selected: list[Item] = []
    ignored: list[Item] = []
    for item in new_items:
        if matches_filters(item, cfg):
            selected.append(item)
        else:
            ignored.append(item)

    if len(selected) > cfg.max_items_cycle:
        overflow = selected[:-cfg.max_items_cycle]
        selected = selected[-cfg.max_items_cycle :]
        ignored.extend(overflow)
        LOG.warning(
            "%s: %d itens excederam MAX_ITEMS_PER_CYCLE=%d; os mais antigos foram ignorados.",
            source.name,
            len(overflow),
            cfg.max_items_cycle,
        )

    if not selected:
        LOG.info("%s: %d item(ns) novo(s), nenhum passou nos filtros.", source.name, len(new_items))
        if not dry_run:
            state.mark_many(source.key, (item.item_id for item in ignored))
            state.update_cache(source.key, result.etag, result.last_modified)
            state.save()
        return 0

    if dry_run:
        LOG.info(
            "%s: %d item(ns) novo(s), %d selecionado(s) (dry-run: estado não será alterado).",
            source.name,
            len(new_items),
            len(selected),
        )
        deliver_items(source, selected, cfg, state, telegram, dry_run=True)
        return 0

    state.mark_many(source.key, (item.item_id for item in ignored))
    try:
        sent, complete = deliver_items(source, selected, cfg, state, telegram, dry_run=False)
    except TelegramError as exc:
        LOG.error("%s: falha ao enviar ao Telegram: %s", source.name, exc)
        state.save()
        return 0

    if complete:
        state.update_cache(source.key, result.etag, result.last_modified)
    state.save()

    LOG.info(
        "%s: %d novo(s), %d enviado(s), %d ignorado(s).",
        source.name,
        len(new_items),
        sent,
        len(ignored),
    )
    return sent


def run_bot(cfg: Config, once: bool = False) -> int:
    state = State(cfg.state_file)
    telegram: Telegram | None = None

    if cfg.dry_run:
        LOG.info("MODO DRY-RUN: nenhuma mensagem será enviada e o estado não será alterado.")
    else:
        telegram = Telegram(cfg)
        me = telegram.get_me()
        username = me.get("username") or me.get("first_name") or "bot"
        LOG.info("Conectado ao Telegram como @%s.", username)

    source_names = ", ".join(f"{source.emoji} {source.name}" for source in cfg.sources)
    LOG.info("Fontes: %s", source_names)
    LOG.info(
        "Intervalo: %ds | atraso entre mensagens: %.1fs | resumo: %s",
        cfg.poll_seconds,
        cfg.send_delay,
        "sim" if cfg.include_summary else "não",
    )
    if cfg.translate_enabled:
        LOG.info(
            "Tradução: ativa (%s), até %d textos por ciclo.",
            cfg.translate_target,
            cfg.translate_max,
        )
    if cfg.include_keywords:
        LOG.info("Filtro incluir: %s", ", ".join(cfg.include_keywords))
    if cfg.exclude_keywords:
        LOG.info("Filtro excluir: %s", ", ".join(cfg.exclude_keywords))
    if cfg.thread_id:
        LOG.info("Destino: %s (tópico %s)", cfg.chat_id, cfg.thread_id)
    else:
        LOG.info("Destino: %s", cfg.chat_id if cfg.chat_id else "(dry-run)")
    LOG.info("Estado: %s", cfg.state_file)

    runtime = Runtime()
    try:
        while True:
            cycle_started = time.monotonic()
            total_sent = 0
            for source in cfg.sources:
                try:
                    total_sent += process_source(source, cfg, state, telegram, runtime, cfg.dry_run)
                except Exception:
                    LOG.exception("%s: erro inesperado no ciclo.", source.name)
            if total_sent:
                LOG.info("Ciclo concluído: %d item(ns) enviado(s).", total_sent)
            else:
                LOG.debug("Ciclo concluído sem novidades.")

            try:
                maybe_send_calendar(cfg, state, telegram, cfg.dry_run)
            except TelegramError as exc:
                LOG.error("Calendário: falha ao enviar ao Telegram: %s", exc)
            except Exception:
                LOG.exception("Calendário: erro inesperado.")

            if once:
                break
            elapsed = time.monotonic() - cycle_started
            wait = max(5.0, cfg.poll_seconds - elapsed)
            LOG.debug("Aguardando %.0fs até o próximo ciclo.", wait)
            time.sleep(wait)
    except KeyboardInterrupt:
        LOG.info("Interrompido pelo usuário.")
    finally:
        if not cfg.dry_run:
            state.save()
    return 0


# ---------------------------------------------------------------------------
# Comandos auxiliares
# ---------------------------------------------------------------------------

def cmd_check(cfg: Config) -> int:
    print("Verificando fontes RSS...\n")
    errors = 0
    for source in cfg.sources:
        started = time.monotonic()
        try:
            result = fetch_feed(source, cfg)
            elapsed = time.monotonic() - started
            items = dedupe_items(result.items)
            if result.not_modified:
                print(f"[OK] {source.name}: feed sem alterações (304) [{elapsed:.1f}s]")
            else:
                print(f"[OK] {source.name}: {len(items)} itens [{elapsed:.1f}s]")
                for item in items[-3:][::-1]:
                    print(f"     • {_truncate(item.title, 110)}")
        except FeedError as exc:
            errors += 1
            print(f"[ERRO] {source.name}: {exc}")
        print(f"     URL: {source.url}\n")

    if cfg.include_keywords:
        print("Filtro incluir:", ", ".join(cfg.include_keywords))
    if cfg.exclude_keywords:
        print("Filtro excluir:", ", ".join(cfg.exclude_keywords))
    if not cfg.include_keywords and not cfg.exclude_keywords:
        print("Filtros: nenhum (todas as notícias passam)")

    print("\nConfiguração carregada com sucesso." if not errors else f"\n{errors} fonte(s) com erro.")
    return 1 if errors else 0


def cmd_preview(cfg: Config, count: int) -> int:
    if count < 1:
        print("Use um número maior que zero.")
        return 2
    found_any = False
    for source in cfg.sources:
        print(f"\n{'=' * 70}\n{source.name}\n{'=' * 70}")
        try:
            result = fetch_feed(source, cfg)
        except FeedError as exc:
            print(f"Erro ao buscar: {exc}")
            continue
        items = dedupe_items(result.items)
        filtered = filter_items(items, cfg)
        chosen = filtered[-count:]
        print(f"{len(items)} item(ns) no feed; {len(filtered)} após filtros; exibindo {len(chosen)}.\n")
        if not chosen:
            print("(nenhum item para exibir)\n")
            continue
        found_any = True
        translator = get_translator(cfg)
        for item in reversed(chosen):
            print(html_to_plain(format_item(item, cfg, translator)))
            print("-" * 70)
    return 0 if found_any else 1


def cmd_test(cfg: Config) -> int:
    telegram = Telegram(cfg)
    me = telegram.get_me()
    username = me.get("username") or me.get("first_name") or "bot"

    chat_label = cfg.chat_id
    try:
        chat = telegram.get_chat()
        chat_label = chat.get("title") or chat.get("username") or chat.get("first_name") or cfg.chat_id
    except TelegramError as exc:
        LOG.warning("Não foi possível consultar o destino (getChat): %s", exc)

    now = _local_time(cfg).strftime("%d/%m/%Y %H:%M:%S")
    sources = ", ".join(source.name for source in cfg.sources)
    topic = f" · tópico {cfg.thread_id}" if cfg.thread_id else ""
    text = (
        "✅ <b>Bot de notícias conectado!</b>\n"
        f"Destino: <b>{_esc(str(chat_label))}</b>{topic}\n"
        f"Fontes: {_esc(sources)}\n"
        f"Intervalo: {cfg.poll_seconds}s\n"
        f"Horário: {now}\n\n"
        "<i>Mensagem de teste. A partir de agora você receberá as notícias novas.</i>"
    )
    telegram.send(text)
    print(f"OK: mensagem de teste enviada para {chat_label} ({cfg.chat_id}) como @{username}.")
    return 0


def cmd_chats(cfg: Config) -> int:
    telegram = Telegram(cfg)
    me = telegram.get_me()
    print(f"Conectado como @{me.get('username') or me.get('first_name') or 'bot'}.\n")
    try:
        chats = telegram.list_chats()
    except TelegramError as exc:
        print(f"Erro ao consultar atualizações: {exc}")
        print("Se houver um webhook configurado, ele precisa ser removido para usar getUpdates.")
        return 1

    if not chats:
        print("Nenhum chat encontrado.")
        print("Envie uma mensagem para o bot ou publique algo no canal/grupo e tente novamente.")
        return 1

    print("Chats encontrados:\n")
    for chat in chats:
        kind = str(chat.get("type") or "?")
        if chat.get("title"):
            name = str(chat["title"])
        else:
            name = " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            ) or "(sem nome)"
        username = chat.get("username")
        suffix = f"  @{username}" if username else ""
        print(f"  {chat.get('id')}  [{kind}]  {name}{suffix}")

    print("\nCopie o id (a primeira coluna) para TELEGRAM_CHAT_ID no .env.")
    print("Para canais públicos também é possível usar @nome_do_canal.")
    return 0


def cmd_reset_state(cfg: Config) -> int:
    if cfg.state_file.exists():
        try:
            cfg.state_file.unlink()
        except OSError as exc:
            print(f"Erro ao apagar {cfg.state_file}: {exc}")
            return 1
        print(f"Estado apagado: {cfg.state_file}")
        print("No próximo ciclo, o bot marcará o feed atual como visto e enviará apenas notícias novas.")
        print("Para enviar as últimas notícias do feed agora, use SEND_BACKLOG=true antes de rodar.")
    else:
        print(f"O arquivo de estado não existe: {cfg.state_file}")
    return 0


def cmd_calendar(cfg: Config, state: State, force: bool, dry_run: bool) -> int:
    """Envia (ou pré-visualiza) a agenda econômica de hoje."""
    today = _local_time(cfg).date()
    if not force and state.calendar_sent == today.isoformat():
        print("A agenda de hoje já foi enviada hoje. Use --force para reenviar.")
        return 0

    try:
        message = build_calendar_message(cfg, today)
    except FeedError as exc:
        print(f"Erro ao buscar o calendário econômico: {exc}")
        return 1

    if message is None:
        print("Nenhum evento relevante para hoje (fim de semana).")
        return 0

    if dry_run:
        print(html_to_plain(message))
        return 0

    telegram = Telegram(cfg)
    telegram.send(message)
    state.calendar_sent = today.isoformat()
    state.save()
    print(f"Agenda econômica de {today.strftime('%d/%m/%Y')} enviada.")
    return 0


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def setup_logging(log_file: Path, verbose: bool) -> None:
    LOG.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOG.handlers.clear()
    LOG.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%d/%m/%Y %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    LOG.addHandler(console)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        LOG.addHandler(file_handler)
    except OSError as exc:
        print(f"Aviso: não foi possível abrir o log em {log_file}: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Envia notícias de mercado (Financial Juice / FXStreet) para o Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python bot.py --test\n"
            "  python bot.py --check\n"
            "  python bot.py --preview 3\n"
            "  python bot.py --calendar --dry-run\n"
            "  python bot.py --once --dry-run\n"
            "  python bot.py\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="executa apenas um ciclo e sai")
    group.add_argument("--test", action="store_true", help="envia uma mensagem de teste ao Telegram")
    group.add_argument("--chats", action="store_true", help="lista chat_ids vistos pelo bot")
    group.add_argument("--check", action="store_true", help="verifica as fontes RSS e a configuração")
    group.add_argument(
        "--preview",
        nargs="?",
        const=3,
        type=int,
        metavar="N",
        help="mostra N mensagens de exemplo de cada fonte (padrão: 3)",
    )
    group.add_argument("--calendar", action="store_true", help="envia agora a agenda econômica de hoje")
    group.add_argument("--reset-state", action="store_true", help="apaga o histórico de itens já vistos")
    parser.add_argument(
        "--force",
        action="store_true",
        help="com --calendar, reenvia mesmo que já tenha sido enviado hoje",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="não envia ao Telegram e não altera o estado; imprime as mensagens",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="mostra logs detalhados")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

    args = build_parser().parse_args(argv)
    if args.test and args.dry_run:
        print("--test não combina com --dry-run; use --preview para ver mensagens sem enviar.")
        return 2

    try:
        read_only = args.check or args.preview is not None or args.reset_state or args.chats
        requires_telegram = not read_only and not args.dry_run
        if args.test:
            requires_telegram = True
        cfg = load_config(require_telegram=requires_telegram)
        cfg.dry_run = bool(args.dry_run)
        if args.chats and not cfg.token:
            raise ConfigError(
                "Preencha TELEGRAM_BOT_TOKEN no arquivo .env para listar os chats."
            )
    except ConfigError as exc:
        print(f"Erro de configuração:\n{exc}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_file, args.verbose)

    try:
        if args.check:
            return cmd_check(cfg)
        if args.preview is not None:
            return cmd_preview(cfg, args.preview)
        if args.chats:
            return cmd_chats(cfg)
        if args.test:
            if cfg.dry_run:
                print("--test não combina com --dry-run; use --preview para ver mensagens sem enviar.")
                return 2
            return cmd_test(cfg)
        if args.reset_state:
            return cmd_reset_state(cfg)
        if args.calendar:
            state = State(cfg.state_file)
            return cmd_calendar(cfg, state, args.force, cfg.dry_run)

        if not cfg.dry_run and (not cfg.token or not cfg.chat_id):
            print(
                "Erro de configuração: preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env.",
                file=sys.stderr,
            )
            return 2
        return run_bot(cfg, once=args.once)
    except TelegramError as exc:
        LOG.error("Telegram: %s", exc)
        return 1
    except KeyboardInterrupt:
        print("\nEncerrado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
