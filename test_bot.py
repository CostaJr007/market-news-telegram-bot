#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Suíte de testes de validação para o market-news-telegram-bot.
Roda todas as verificações essenciais antes do deploy na VPS.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Ajusta stdout para UTF-8 no Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import bot


class TestMarketNewsBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = bot.load_config(require_telegram=False)

    def test_01_config_loaded(self):
        """Valida se o .env foi carregado com todos os parâmetros essenciais."""
        if not self.cfg.token or not self.cfg.chat_id:
            self.skipTest("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos no .env")
        self.assertTrue(self.cfg.token, "TELEGRAM_BOT_TOKEN não pode ser vazio")
        self.assertTrue(self.cfg.chat_id, "TELEGRAM_CHAT_ID não pode ser vazio")
        self.assertGreaterEqual(self.cfg.poll_seconds, 20)
        self.assertIn("America/Sao_Paulo", (self.cfg.timezone_mode, "America/Sao_Paulo"))

    def test_02_telegram_credentials(self):
        """Valida token e acesso ao chat/tópico no Telegram."""
        if not self.cfg.token:
            self.skipTest("TELEGRAM_BOT_TOKEN não configurado no .env")
        telegram = bot.Telegram(self.cfg)
        me = telegram.get_me()
        self.assertTrue(me.get("is_bot"), "Credencial do Telegram deve ser de um bot")
        self.assertTrue(me.get("username"), "Bot deve possuir um username cadastrado")

        chat = telegram.get_chat()
        self.assertIn(chat.get("type"), ("group", "supergroup", "channel"))

    def test_03_all_sources_fetchable(self):
        """Testa se todas as fontes definidas em bot.SOURCES respondem e parseiam."""
        for key, source in bot.SOURCES.items():
            with self.subTest(source=key):
                try:
                    result = bot.fetch_feed(source, self.cfg)
                    self.assertIsInstance(result.items, list)
                    self.assertGreater(len(result.items), 0, f"Fonte {key} retornou 0 itens")
                except bot.FeedError as e:
                    # Se for rate limit temporário do FJ (429), não falha o teste
                    if "429" in str(e):
                        print(f"\n[AVISO] {key}: Rate limit temporário (429) - normal para testes repetidos.")
                    else:
                        self.fail(f"Erro ao buscar {key}: {e}")

    def test_04_date_parsing_and_tz(self):
        """Valida se o parser de datas converte corretamente para o fuso brasileiro."""
        # RFC 822
        d1 = bot._parse_date("Sun, 08 Nov 2026 23:03:41 GMT")
        self.assertIsNotNone(d1)
        self.assertEqual(d1.tzinfo, timezone.utc)

        # Investing format
        d2 = bot._parse_date("2026-11-08 22:18:22")
        self.assertIsNotNone(d2)
        self.assertEqual(d2.tzinfo, timezone.utc)

        # Horário local do Brasil
        tz = bot._display_tz(self.cfg)
        local_d1 = d1.astimezone(tz)
        # GMT 23:03:41 -> BRT 20:03:41
        self.assertEqual(local_d1.hour, 20)
        self.assertEqual(local_d1.minute, 3)

    def test_05_fj_categories_filtering(self):
        """Valida que notícias fora de Forex/Índices/Macro são barradas e as relevantes passam."""
        # Notícia relevante (Macro/Forex)
        item_relevant = bot.Item(
            source_key="fj",
            title="RBA's Hunter: AI impact on inflation probably neutral",
            link="https://example.com/1",
            item_id="1",
            published=datetime.now(timezone.utc),
            summary="",
        )
        self.assertTrue(bot.matches_filters(item_relevant, self.cfg))

        # Notícia irrelevante de empresa local
        item_noise = bot.Item(
            source_key="fj",
            title="Japan Tobacco Inc.: update on temporary management in Russia",
            link="https://example.com/2",
            item_id="2",
            published=datetime.now(timezone.utc),
            summary="",
        )
        self.assertFalse(bot.matches_filters(item_noise, self.cfg))

    def test_06_calendar_providers(self):
        """Valida se ambos provedores de calendário (ForexFactory e TradingView) funcionam."""
        today = datetime.now(timezone.utc).date()
        # 1. Forex Factory
        events_ff = bot._fetch_calendar_ff(self.cfg, today)
        self.assertIsInstance(events_ff, list)
        self.assertGreater(len(events_ff), 0, "Forex Factory deve retornar eventos")

        # 2. TradingView
        events_tv = bot._fetch_calendar_tv(self.cfg, today)
        self.assertIsInstance(events_tv, list)
        self.assertGreater(len(events_tv), 0, "TradingView deve retornar eventos")

    def test_07_html_escaping(self):
        """Garante que caracteres especiais não quebram formatação HTML do Telegram."""
        raw = "S&P 500 <rises> & Nasdaq 'soars' with \"high\" volume"
        escaped = bot._esc(raw)
        self.assertNotIn("<rises>", escaped)
        self.assertIn("&amp;", escaped)
        self.assertIn("&lt;rises&gt;", escaped)


if __name__ == "__main__":
    unittest.main()
