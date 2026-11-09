#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Suíte de Testes Unitários Completa: Pipeline de Notícias (End-to-End).
Testa cada estágio: Fetch -> Parse -> Dedupe -> Filter -> Format -> State -> Delivery.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

# Ajusta stdout para UTF-8 no Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import bot


class TestNewsPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = bot.load_config(require_telegram=False)

    def setUp(self):
        # Cria diretório temporário para arquivos de estado durante os testes
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "test_state.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. PARSING DE FEEDS (RSS 2.0, Datas e Formatos)
    # -------------------------------------------------------------------------
    def test_01_feed_parsing_standard_rss(self):
        """Valida a extração de título, link, guid, data e descrição de um RSS 2.0."""
        sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Market Feed</title>
                <item>
                    <title>Federal Reserve Signals Interest Rate Cut Ahead</title>
                    <link>https://example.com/news/101</link>
                    <guid isPermaLink="false">guid-101</guid>
                    <pubDate>Sun, 08 Nov 2026 21:00:00 GMT</pubDate>
                    <description>Fed Chairman hints at monetary policy easing.</description>
                </item>
            </channel>
        </rss>"""
        source = bot.SOURCES["fxstreet"]
        items = bot.parse_feed(sample_xml, source)

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.source_key, "fxstreet")
        self.assertEqual(item.item_id, "guid-101")
        self.assertEqual(item.title, "Federal Reserve Signals Interest Rate Cut Ahead")
        self.assertEqual(item.link, "https://example.com/news/101")
        self.assertIn("Fed Chairman hints", item.summary)
        self.assertEqual(item.published.year, 2026)

    def test_02_feed_parsing_investing_date_format(self):
        """Valida que datas no formato não-padrão do Investing (YYYY-MM-DD HH:MM:SS) são aceitas."""
        investing_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Investing.com</title>
                <item>
                    <title>Dollar Index gains following jobs report</title>
                    <link>https://br.investing.com/news/1</link>
                    <guid>inv-999</guid>
                    <pubDate>2026-11-08 22:15:30</pubDate>
                    <description>US Dollar shows resilience.</description>
                </item>
            </channel>
        </rss>"""
        source = bot.SOURCES["investing"]
        items = bot.parse_feed(investing_xml, source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].published.hour, 22)
        self.assertEqual(items[0].published.minute, 15)
        self.assertEqual(items[0].published.second, 30)

    # -------------------------------------------------------------------------
    # 2. DEDUPLICAÇÃO DE ITENS
    # -------------------------------------------------------------------------
    def test_03_deduplication_removes_identical_ids(self):
        """Garante que itens com o mesmo ID ou GUID sejam deduplicados em lote."""
        now = datetime.now(timezone.utc)
        items = [
            bot.Item(source_key="fj", title="A", link="https://1", item_id="ID_1", published=now, summary=""),
            bot.Item(source_key="fj", title="A-duplicate", link="https://1", item_id="ID_1", published=now, summary=""),
            bot.Item(source_key="fj", title="B", link="https://2", item_id="ID_2", published=now, summary=""),
        ]
        deduped = bot.dedupe_items(items)
        self.assertEqual(len(deduped), 2)
        self.assertEqual([it.item_id for it in deduped], ["ID_1", "ID_2"])

    # -------------------------------------------------------------------------
    # 3. CLASSIFICAÇÃO E FILTROS (Financial Juice & FXStreet)
    # -------------------------------------------------------------------------
    def test_04_financial_juice_category_classifier(self):
        """Testa o classificador de FJ: aceita Forex/Índices/Macro e rejeita ruído."""
        now = datetime.now(timezone.utc)
        
        # Itens válidos que DEVEM passar:
        valid_news = [
            "China PBOC likely to fix yuan mid-point at 6.6799 per dollar",
            "S&P 500 futures rise 0.5% ahead of inflation data",
            "US Treasury announces sanctions on trade shipping routes",
            "ECB Lagarde: Inflation target in sight but caution needed",
            "Oil prices jump as Strait of Hormuz tensions escalate",
            "Gold hits new weekly high amid currency fluctuations",
        ]
        for title in valid_news:
            item = bot.Item(source_key="fj", title=title, link="https://fj", item_id=title, published=now, summary="")
            self.assertTrue(bot.matches_filters(item, self.cfg), f"Deveria ter passado: {title}")

        # Itens corporativos irrelevantes que DEVEM ser filtrados:
        noise_news = [
            "Japan Tobacco Inc.: update on temporary management in Russian operations",
            "Local soccer championship reaches quarterfinals in regional cup",
            "Actor wins award at annual film festival ceremony",
            "Nvidia CEO personal note to company employees regarding internal affairs",
        ]
        for title in noise_news:
            item = bot.Item(source_key="fj", title=title, link="https://fj", item_id=title, published=now, summary="")
            self.assertFalse(bot.matches_filters(item, self.cfg), f"Deveria ter sido filtrado: {title}")

    def test_05_keyword_word_boundary_precision(self):
        """Garante que palavras curtas como 'war' e 'oil' não tenham falsos positivos."""
        now = datetime.now(timezone.utc)
        
        # 'war' em 'forward' não pode dar match
        item_false_war = bot.Item(
            source_key="fj",
            title="Company provides forward guidance for next fiscal quarter",
            link="https://ex",
            item_id="1",
            published=now,
            summary="",
        )
        self.assertFalse(bot.matches_filters(item_false_war, self.cfg))

        # 'war' em 'trade war' deve dar match
        item_true_war = bot.Item(
            source_key="fj",
            title="New tariffs threaten trade war escalation between partners",
            link="https://ex",
            item_id="2",
            published=now,
            summary="",
        )
        self.assertTrue(bot.matches_filters(item_true_war, self.cfg))

    # -------------------------------------------------------------------------
    # 4. FORMATAÇÃO HTML E FUSO HORÁRIO (São Paulo / Brasília)
    # -------------------------------------------------------------------------
    def test_06_format_single_item_html(self):
        """Valida se a mensagem unitária é gerada com HTML seguro, links e horário de Brasília."""
        # Cria um item publicado às 18:30 UTC
        pub_utc = datetime(2026, 11, 8, 18, 30, 0, tzinfo=timezone.utc)
        item = bot.Item(
            source_key="fxstreet",
            title="EUR/USD touches 1.0850 & S&P gains",
            link="https://www.fxstreet.com/news/100",
            item_id="fx-100",
            published=pub_utc,
            summary="Euro moves higher against the greenback.",
        )
        msg = bot.format_item(item, self.cfg)

        # 18:30 UTC no fuso America/Sao_Paulo (UTC-3) é 15:30
        self.assertIn("15:30", msg)
        self.assertIn("🔵 <b>FXStreet</b>", msg)
        self.assertIn("&amp;", msg)  # & deve ser escapado
        self.assertIn('<a href="https://www.fxstreet.com/news/100">', msg)
        self.assertIn("Euro moves higher", msg)

    def test_07_format_digest_burst(self):
        """Valida que rajadas de notícias viram resumo agrupado."""
        source = bot.SOURCES["fj"]
        now = datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)
        items = [
            bot.Item(source_key="fj", title=f"Notícia Macro {i}", link=f"https://fj/{i}", item_id=f"id-{i}", published=now, summary="")
            for i in range(1, 7)
        ]
        # SINGLE_MAX é 4; 6 itens devem gerar resumo
        chunks = bot.format_digest(source, items, self.cfg)
        self.assertGreater(len(chunks), 0)
        digest_text, digested_items = chunks[0]
        self.assertIn("🟠 <b>Financial Juice</b> · <b>6</b> novas", digest_text)
        self.assertEqual(len(digested_items), 6)

    # -------------------------------------------------------------------------
    # 5. GERENCIAMENTO DE ESTADO (State Persistence)
    # -------------------------------------------------------------------------
    def test_08_state_persistence_across_cycles(self):
        """Valida gravação e recarregamento do state.json no disco."""
        state1 = bot.State(self.state_file)
        self.assertFalse(state1.is_seen("fj", "item_abc"))
        
        state1.mark_many("fj", ["item_abc", "item_xyz"])
        state1.save()
        self.assertTrue(self.state_file.exists())

        # Novo carregamento simulando reinício do bot
        state2 = bot.State(self.state_file)
        self.assertTrue(state2.is_seen("fj", "item_abc"))
        self.assertTrue(state2.is_seen("fj", "item_xyz"))
        self.assertFalse(state2.is_seen("fj", "item_novo"))

    # -------------------------------------------------------------------------
    # 6. PIPELINE END-TO-END (Simulação Completa com Mock Telegram)
    # -------------------------------------------------------------------------
    def test_09_end_to_end_pipeline_flow(self):
        """Simula um ciclo de execução do process_source completo:
        1. 3 notícias chegam (2 válidas, 1 ruído corporativo).
        2. Apenas as 2 válidas passam pelo filtro.
        3. As 2 mensagens são enviadas via Telegram.
        4. O estado é salvo e nenhum reenvio ocorre no ciclo seguinte.
        """
        source = bot.SOURCES["fxstreet"]
        state = bot.State(self.state_file)
        # Marca a fonte como já inicializada para não ativar o modo primeira execução
        state.mark_many("fxstreet", ["bootstrap_id"])
        
        mock_telegram = MagicMock()
        runtime = bot.Runtime()

        now = datetime.now(timezone.utc)
        raw_items = [
            bot.Item(source_key="fxstreet", title="Fed prepares interest rate cut decision", link="https://fx/1", item_id="fx-1", published=now, summary=""),
            bot.Item(source_key="fxstreet", title="Local regional store opens new branch", link="https://fx/2", item_id="fx-2", published=now, summary=""),
            bot.Item(source_key="fxstreet", title="ECB inflation report signals rate pause", link="https://fx/3", item_id="fx-3", published=now, summary=""),
        ]

        # Simula fetch_feed retornando os itens
        original_fetch = bot.fetch_feed
        try:
            bot.fetch_feed = lambda s, c, h=None: bot.FetchResult(source=s, items=raw_items, not_modified=False)
            
            # Ciclo 1: Deve enviar 2 notícias (rejeitando o ruído da loja)
            processed_count = bot.process_source(source, self.cfg, state, mock_telegram, runtime, dry_run=False)
            self.assertEqual(processed_count, 2)
            self.assertEqual(mock_telegram.send.call_count, 2)
            
            # Ciclo 2: Se rodar de novo com os mesmos itens, NADA deve ser enviado (já foram vistos)
            mock_telegram.reset_mock()
            processed_again = bot.process_source(source, self.cfg, state, mock_telegram, runtime, dry_run=False)
            self.assertEqual(processed_again, 0)
            mock_telegram.send.assert_not_called()
        finally:
            bot.fetch_feed = original_fetch


if __name__ == "__main__":
    unittest.main()
