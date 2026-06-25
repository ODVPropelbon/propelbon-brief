"""
Propelbon Daily Brief — script autónomo (stack 100% gratuito)
Requisitos: ver requirements.txt
Secrets necesarios (env vars o GitHub Secrets):
  GEMINI_API_KEY       (Google AI Studio — gratis: aistudio.google.com)
  SLACK_BOT_TOKEN      (token del bot con permisos channels:history + chat:write)
  SLACK_CHANNEL_ID     (C0B93TX9SQL)
  SLACK_WEBHOOK_URL    (opcional, para enviar como bot "Propelbon Radar")
  TAVILY_API_KEY       (Tavily free tier: 1.000 búsquedas/mes gratis)
"""

import os
import asyncio
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from tavily import TavilyClient


# ── Configuración ────────────────────────────────────────────────────────────

GEMINI_API_KEY    = os.environ["GEMINI_API_KEY"]
SLACK_BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID  = os.environ.get("SLACK_CHANNEL_ID", "C0B93TX9SQL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")  # opcional
TAVILY_API_KEY    = os.environ["TAVILY_API_KEY"]

MADRID_TZ = ZoneInfo("Europe/Madrid")
TODAY = datetime.now(MADRID_TZ).strftime("%d %B %Y")
TODAY_SHORT = datetime.now(MADRID_TZ).strftime("%-d %b %Y")

# Fuentes a rotar cada día (el script elige 3 de cada pool)
AFFILIATE_SOURCES = [
    "https://hellopartner.com/tag/newsdesk/",
    "https://www.affiversemedia.com/news/",
    "https://performancein.com/news/",
    "https://www.awin.com/us/news-and-events/awin-news",
    "https://martech.org/topic/performance-marketing/",
    "https://www.thedrum.com/topic/performance-marketing",
]

ECOMMERCE_SOURCES = [
    "https://www.ecommercenews.eu/news/",
    "https://channelx.world",
    "https://www.retaildive.com/topic/e-commerce/",
    "https://www.digitalcommerce360.com/topic/ecommerce/",
    "https://internetretailing.net/",
    "https://practicalecommerce.com/",
]

# Día de la semana como índice para rotar fuentes (0=lunes)
_dow = datetime.now(MADRID_TZ).weekday()
SELECTED_AFFILIATE = AFFILIATE_SOURCES[(_dow * 3) % len(AFFILIATE_SOURCES):(_dow * 3) % len(AFFILIATE_SOURCES) + 3]
SELECTED_ECOMMERCE = ECOMMERCE_SOURCES[(_dow * 3) % len(ECOMMERCE_SOURCES):(_dow * 3) % len(ECOMMERCE_SOURCES) + 3]


# ── PASO 0: Leer historial de Slack ─────────────────────────────────────────

def get_published_urls_and_topics(limit_messages: int = 200) -> tuple[set[str], list[str]]:
    """Devuelve (conjunto de URLs ya publicadas, lista de titulares ya publicados)."""
    import re
    client = WebClient(token=SLACK_BOT_TOKEN)
    urls: set[str] = set()
    topics: list[str] = []
    cursor = None

    while True:
        kwargs = {"channel": SLACK_CHANNEL_ID, "limit": 100}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            resp = client.conversations_history(**kwargs)
        except SlackApiError as e:
            print(f"[Slack] Error leyendo canal: {e}")
            break

        for msg in resp.get("messages", []):
            text = msg.get("text", "")
            # URLs en formato <URL|texto>
            for url in re.findall(r"<(https?://[^|>]+)[|>]", text):
                urls.add(url)
            # Titulares en negrita *Titular*
            for title in re.findall(r"\*([^*]{10,120})\*", text):
                topics.append(title.strip())

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor or len(urls) > limit_messages:
            break

    print(f"[Slack] {len(urls)} URLs y {len(topics)} titulares cargados del historial")
    return urls, topics


# ── PASO 1: Búsquedas de noticias ───────────────────────────────────────────

def search_news(tavily: TavilyClient) -> list[dict]:
    """Lanza búsquedas en paralelo y devuelve lista de resultados."""
    queries = [
        f"ecommerce news today {TODAY}",
        f"ecommerce España noticias {TODAY}",
        f"affiliate marketing news {TODAY}",
        f"performance marketing news {TODAY}",
        f"affiliate network news Awin CJ impact.com Tradedoubler {TODAY}",
        f"TikTok Shop ecommerce Europe {TODAY}",
    ]

    results = []
    for q in queries:
        try:
            r = tavily.search(query=q, max_results=5, search_depth="advanced")
            results.extend(r.get("results", []))
        except Exception as e:
            print(f"[Tavily] Error en '{q}': {e}")

    print(f"[Tavily] {len(results)} resultados de búsqueda obtenidos")
    return results


async def fetch_source(client: httpx.AsyncClient, url: str) -> str:
    """Hace fetch de una URL y devuelve el texto (primeros 3000 chars)."""
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        return r.text[:3000]
    except Exception as e:
        return f"[Error fetching {url}: {e}]"


async def fetch_all_sources() -> dict[str, str]:
    """Fetch paralelo de todas las fuentes seleccionadas."""
    all_sources = SELECTED_AFFILIATE + SELECTED_ECOMMERCE
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        tasks = {url: fetch_source(client, url) for url in all_sources}
        results = {}
        for url, coro in tasks.items():
            results[url] = await coro
    print(f"[HTTP] {len(results)} fuentes fetched")
    return results


# ── PASO 2-4: Gemini redacta el briefing ────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente de noticias de Propelbon, empresa española de marketing de afiliación y performance que trabaja con anunciantes ecommerce en España y Europa.

Tu tarea: redactar el briefing diario de noticias para el canal #noticias de Slack.

Estilo:
- Idioma: español (términos técnicos en inglés cuando son estándar del sector)
- Tono: directo, analítico, sin fluff. Como un colega senior del sector.
- Perspectiva siempre desde Propelbon: ¿qué significa esto para nuestros anunciantes o publishers?

Formato de salida: mrkdwn de Slack (usar *negrita*, _cursiva_, <URL|texto>).
"""

def build_user_prompt(
    search_results: list[dict],
    source_texts: dict[str, str],
    published_urls: set[str],
    published_topics: list[str],
) -> str:
    # Serializar resultados de búsqueda
    search_block = "\n\n".join([
        f"TÍTULO: {r.get('title','')}\nURL: {r.get('url','')}\nRESUMEN: {r.get('content','')[:400]}"
        for r in search_results
    ])

    # Serializar fuentes directas
    sources_block = "\n\n---\n\n".join([
        f"FUENTE: {url}\n{text[:1500]}"
        for url, text in source_texts.items()
    ])

    # Historial de URLs ya publicadas (últimas 100 para no saturar el prompt)
    published_list = "\n".join(list(published_urls)[:100])
    published_topics_list = "\n".join(published_topics[:80])

    return f"""Fecha de hoy: {TODAY}

=== HISTORIAL DE URLS YA PUBLICADAS EN #noticias (NO repetir) ===
{published_list}

=== TITULARES YA PUBLICADOS (NO repetir temas sustancialmente iguales) ===
{published_topics_list}

=== RESULTADOS DE BÚSQUEDA ===
{search_block}

=== CONTENIDO DE FUENTES DIRECTAS ===
{sources_block}

---

Con todo lo anterior, redacta el briefing diario siguiendo estas reglas ESTRICTAS:

1. FILTRO DE DUPLICADOS: Descarta cualquier noticia cuya URL ya esté en el historial, cuyo titular sea equivalente a uno ya publicado, o cuyo tema sea sustancialmente idéntico a uno ya cubierto.

2. SELECCIÓN:
   - ECOMMERCE: 2-4 items. Priorizar plataformas (Amazon, Shopify, TikTok Shop), regulación EU, grandes retailers, tendencias España/Europa.
   - AFILIACIÓN & PERFORMANCE: 2-4 items. Priorizar redes (Awin, CJ, Tradedoubler, impact.com), tracking, tecnología, nuevos programas.
   - Máximo 2 items del mismo dominio en el mismo briefing.
   - Solo noticias de las últimas 72h salvo que sean de alto impacto y no cubiertas antes.

3. FORMATO DE SALIDA (mrkdwn exacto, sin explicaciones adicionales):

📰 *Propelbon Daily Brief · {TODAY_SHORT}*
_[N fuentes consultadas · 0 repeticiones de briefings anteriores]_

━━━━━━━━━━
📦 *ECOMMERCE*

*[Titular noticia 1]*
[2-3 frases resumen con datos concretos]
💡 _Para Propelbon: [implicación concreta]_
🔗 <URL|Leer noticia>

[repetir por cada item ecommerce]

━━━━━━━━━━
🤝 *AFILIACIÓN & PERFORMANCE*

[items igual]

━━━━━━━━━━
⚡ *SEÑAL DEL DÍA*
*[Titular del item de mayor impacto]*
[Contexto ampliado + acción concreta para esta semana]
🔗 <URL|Leer noticia>

IMPORTANTE: devuelve SOLO el mensaje mrkdwn, sin texto previo ni posterior.
"""


def generate_briefing(
    search_results: list[dict],
    source_texts: dict[str, str],
    published_urls: set[str],
    published_topics: list[str],
) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    user_prompt = build_user_prompt(search_results, source_texts, published_urls, published_topics)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
        contents=user_prompt,
    )
    return response.text.strip()


# ── PASO 5: Enviar a Slack ───────────────────────────────────────────────────

def send_to_slack(text: str) -> str:
    """Intenta primero el webhook (bot Propelbon Radar), luego slack_sdk."""
    # Opción 1: webhook (mantiene la identidad del bot)
    if SLACK_WEBHOOK_URL:
        try:
            r = httpx.post(
                SLACK_WEBHOOK_URL,
                json={"text": text},
                timeout=10,
            )
            if r.status_code == 200 and r.text == "ok":
                print("[Slack] Enviado vía webhook ✓")
                return "webhook"
            else:
                print(f"[Slack] Webhook falló ({r.status_code}), usando bot token...")
        except Exception as e:
            print(f"[Slack] Webhook error: {e}, usando bot token...")

    # Opción 2: bot token
    client = WebClient(token=SLACK_BOT_TOKEN)
    resp = client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=text)
    print(f"[Slack] Enviado vía bot token ✓ — {resp['message']['ts']}")
    return resp["message"]["ts"]


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n=== Propelbon Daily Brief — {TODAY} ===\n")

    # PASO 0
    print("→ PASO 0: Leyendo historial de Slack...")
    published_urls, published_topics = get_published_urls_and_topics()

    # PASO 1
    print("→ PASO 1: Buscando noticias...")
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    search_results = search_news(tavily)
    source_texts = await fetch_all_sources()

    # PASO 2-4
    print("→ PASO 2-4: Redactando briefing con Gemini Flash...")
    briefing = generate_briefing(search_results, source_texts, published_urls, published_topics)
    print("\n--- BRIEFING GENERADO ---")
    print(briefing)
    print("-------------------------\n")

    # PASO 5
    print("→ PASO 5: Enviando a Slack...")
    send_to_slack(briefing)
    print("\n✅ Briefing enviado correctamente.\n")


if __name__ == "__main__":
    asyncio.run(main())
