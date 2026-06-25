"""
Propelbon Daily Brief â script autÃ³nomo (stack 100% gratuito)
Secrets necesarios (env vars o GitHub Secrets):
  GROQ_API_KEY         (Groq â gratis: console.groq.com, modelo llama-3.3-70b)
  SLACK_BOT_TOKEN      (token del bot con permisos channels:history + chat:write)
  SLACK_CHANNEL_ID     (C0B93TX9SQL)
  SLACK_WEBHOOK_URL    (opcional)
  TAVILY_API_KEY       (Tavily free tier: 1.000 bÃºsquedas/mes gratis)
"""

import os
import asyncio
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

from groq import Groq
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from tavily import TavilyClient


# ââ ConfiguraciÃ³n ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
SLACK_BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID  = os.environ.get("SLACK_CHANNEL_ID", "C0B93TX9SQL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
TAVILY_API_KEY    = os.environ["TAVILY_API_KEY"]

MADRID_TZ = ZoneInfo("Europe/Madrid")
TODAY = datetime.now(MADRID_TZ).strftime("%d %B %Y")
TODAY_SHORT = datetime.now(MADRID_TZ).strftime("%-d %b %Y")

# ââ Fuentes: blogs de redes de afiliaciÃ³n ââââââââââââââââââââââââââââââââââââ
NETWORK_BLOGS = [
    "https://www.awin.com/us/news-and-events/awin-news",
    "https://blog.tradedoubler.com/",
    "https://blog.admitad.com/en/",
    "https://impact.com/resources/blog/",
    "https://partnerize.com/resources/blog/",
    "https://www.webgains.com/public/uk/blog/",
    "https://tradetracker.com/blog/",
    "https://blog.cj.com/",
]

# ââ Fuentes: blogs especializados EN + ES ââââââââââââââââââââââââââââââââââââ
INDUSTRY_BLOGS = [
    "https://hellopartner.com/tag/newsdesk/",
    "https://www.affiversemedia.com/news/",
    "https://performancein.com/news/",
    "https://martech.org/topic/performance-marketing/",
    "https://www.thedrum.com/topic/performance-marketing",
    "https://www.accelerationpartners.com/resources/blog/",
    "https://www.affiliatesummit.com/blog/",
    "https://www.marketingdirecto.com/marketing-digital/",
    "https://iabspain.es/category/noticias/",
]

# ââ Fuentes: ecommerce âââââââââââââââââââââââââââââââââââââââââââââââââââââââ
ECOMMERCE_SOURCES = [
    "https://www.ecommercenews.eu/news/",
    "https://channelx.world",
    "https://www.retaildive.com/topic/e-commerce/",
    "https://www.digitalcommerce360.com/topic/ecommerce/",
    "https://internetretailing.net/",
    "https://practicalecommerce.com/",
    "https://www.ecommerce-news.es/",
    "https://www.shopify.com/blog",
]

# Rotar fuentes por dÃ­a (0=lunes): seleccionar 3 de cada pool
_dow = datetime.now(MADRID_TZ).weekday()
def _rotate(pool, n=3):
    start = (_dow * n) % len(pool)
    return (pool + pool)[start:start + n]

SELECTED_NETWORKS  = _rotate(NETWORK_BLOGS, 3)
SELECTED_INDUSTRY  = _rotate(INDUSTRY_BLOGS, 3)
SELECTED_ECOMMERCE = _rotate(ECOMMERCE_SOURCES, 3)


# ââ PASO 0: Leer historial de Slack âââââââââââââââââââââââââââââââââââââââââ

def get_published_urls_and_topics(limit_messages: int = 200) -> tuple[set[str], list[str]]:
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
            for url in re.findall(r"<(https?://[^|>]+)[|>]", text):
                urls.add(url)
            for title in re.findall(r"\*([^*]{10,120})\*", text):
                topics.append(title.strip())

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor or len(urls) > limit_messages:
            break

    print(f"[Slack] {len(urls)} URLs y {len(topics)} titulares cargados del historial")
    return urls, topics


# ââ PASO 1: BÃºsquedas de noticias âââââââââââââââââââââââââââââââââââââââââââ

def search_news(tavily: TavilyClient) -> list[dict]:
    queries = [
        # Noticias generales ecommerce
        f"ecommerce news today {TODAY}",
        f"ecommerce EspaÃ±a noticias {TODAY}",
        # Noticias de afiliaciÃ³n
        f"affiliate marketing news {TODAY}",
        f"performance marketing news {TODAY}",
        # Noticias especÃ­ficas de redes
        f"Awin Tradedoubler impact.com Partnerize affiliate network news {TODAY}",
        f"Admitad Webgains TradeTracker CJ affiliate network news {TODAY}",
        # Social: Twitter/X
        f"site:x.com OR site:twitter.com affiliate marketing awin tradedoubler impact partnerize {TODAY}",
        f"site:x.com OR site:twitter.com ecommerce performance marketing {TODAY}",
        # LinkedIn
        f"site:linkedin.com affiliate marketing performance ecommerce {TODAY}",
        # Tendencias
        f"TikTok Shop ecommerce Europe affiliate {TODAY}",
        f"cookie tracking privacy affiliate marketing {TODAY}",
    ]

    results = []
    for q in queries:
        try:
            r = tavily.search(query=q, max_results=4, search_depth="advanced")
            results.extend(r.get("results", []))
        except Exception as e:
            print(f"[Tavily] Error en '{q}': {e}")

    print(f"[Tavily] {len(results)} resultados de bÃºsqueda obtenidos")
    return results


async def fetch_source(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        return r.text[:3000]
    except Exception as e:
        return f"[Error fetching {url}: {e}]"


async def fetch_all_sources() -> dict[str, str]:
    all_sources = SELECTED_NETWORKS + SELECTED_INDUSTRY + SELECTED_ECOMMERCE
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (compatible; PropelbonBot/1.0)"}) as client:
        coros = {url: fetch_source(client, url) for url in all_sources}
        results = {}
        for url, coro in coros.items():
            results[url] = await coro
    print(f"[HTTP] {len(results)} fuentes fetched ({len(SELECTED_NETWORKS)} redes + {len(SELECTED_INDUSTRY)} industria + {len(SELECTED_ECOMMERCE)} ecommerce)")
    return results


# ââ PASO 2-4: Groq (Llama 3.3 70B) redacta el briefing âââââââââââââââââââââ

SYSTEM_PROMPT = """Eres el asistente de noticias de Propelbon, empresa espaÃ±ola de marketing de afiliaciÃ³n y performance que trabaja con anunciantes ecommerce en EspaÃ±a y Europa.

Tu tarea: redactar el briefing diario de noticias para el canal #noticias de Slack.

Fuentes que se consultan cada dÃ­a: blogs oficiales de redes de afiliaciÃ³n (Awin, Tradedoubler, Admitad, impact.com, Partnerize, Webgains, TradeTracker, CJ), blogs especializados (PerformanceIN, Hello Partner, Affiverse, MarTech, Marketing Directo, IAB Spain), Twitter/X y LinkedIn de estas redes, y medios de ecommerce (eCommerce News, Retail Dive, Digital Commerce 360, etc.).

Estilo:
- Idioma: espaÃ±ol (tÃ©rminos tÃ©cnicos en inglÃ©s cuando son estÃ¡ndar del sector)
- Tono: directo, analÃ­tico, sin fluff. Como un colega senior del sector.
- Perspectiva siempre desde Propelbon: Â¿quÃ© significa esto para nuestros anunciantes o publishers?
- Si la noticia viene de Twitter/X o LinkedIn, mencionarlo sutilmente (ej: "segÃºn publica en X...")

Formato de salida: mrkdwn de Slack (usar *negrita*, _cursiva_, <URL|texto>).
"""

def build_user_prompt(search_results, source_texts, published_urls, published_topics):
    search_block = "\n\n".join([
        f"TÃTULO: {r.get('title','')}\nURL: {r.get('url','')}\nRESUMEN: {r.get('content','')[:200]}"
        for r in search_results
    ])
    sources_block = "\n\n---\n\n".join([
        f"FUENTE: {url}\n{text[:600]}"
        for url, text in source_texts.items()
    ])
    published_list = "\n".join(list(published_urls)[:40])
    published_topics_list = "\n".join(published_topics[:30])

    return f"""Fecha de hoy: {TODAY}

=== HISTORIAL DE URLS YA PUBLICADAS EN #noticias (NO repetir) ===
{published_list}

=== TITULARES YA PUBLICADOS (NO repetir temas sustancialmente iguales) ===
{published_topics_list}

=== RESULTADOS DE BÃSQUEDA (incluye Twitter/X, LinkedIn, blogs) ===
{search_block}

=== CONTENIDO DE FUENTES DIRECTAS (blogs de redes + industria + ecommerce) ===
{sources_block}

---

Redacta el briefing diario siguiendo estas reglas ESTRICTAS:

1. FILTRO DE DUPLICADOS: Descarta cualquier noticia cuya URL ya estÃ© en el historial o cuyo tema sea sustancialmente idÃ©ntico a uno ya cubierto.

2. SELECCIÃN:
   - ECOMMERCE: 2-4 items. Priorizar plataformas (Amazon, Shopify, TikTok Shop), regulaciÃ³n EU, grandes retailers, tendencias EspaÃ±a/Europa.
   - AFILIACIÃN & PERFORMANCE: 2-4 items. Priorizar noticias de redes (Awin, Tradedoubler, Admitad, impact.com, Partnerize, Webgains, CJ, TradeTracker), tracking/privacidad, tecnologÃ­a, nuevos programas. Incluir seÃ±ales de Twitter/X o LinkedIn si son relevantes.
   - MÃ¡ximo 2 items del mismo dominio por briefing.
   - Solo noticias de las Ãºltimas 72h salvo que sean de alto impacto.

3. FORMATO DE SALIDA (mrkdwn exacto, sin texto adicional):

ð° *Propelbon Daily Brief Â· {TODAY_SHORT}*
_[N fuentes consultadas Â· redes: Awin, TD, Admitad, impact, Partnerize, Webgains, CJ, TT]_

ââââââââââ
ð¦ *ECOMMERCE*

*[Titular noticia 1]*
[2-3 frases resumen con datos concretos]
ð¡ _Para Propelbon: [implicaciÃ³n concreta]_
ð <URL|Leer noticia>

[repetir por cada item ecommerce]

ââââââââââ
ð¤ *AFILIACIÃN & PERFORMANCE*

[items igual]

ââââââââââ
â¡ *SEÃAL DEL DÃA*
*[Titular del item de mayor impacto estratÃ©gico]*
[Contexto ampliado + acciÃ³n concreta para esta semana]
ð <URL|Leer noticia>

IMPORTANTE: devuelve SOLO el mensaje mrkdwn, sin texto previo ni posterior.
"""


def generate_briefing(search_results, source_texts, published_urls, published_topics):
    client = Groq(api_key=GROQ_API_KEY)
    user_prompt = build_user_prompt(search_results, source_texts, published_urls, published_topics)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.5,
        max_tokens=2500,
    )
    return response.choices[0].message.content.strip()


# ââ PASO 5: Enviar a Slack âââââââââââââââââââââââââââââââââââââââââââââââââââ

def send_to_slack(text: str) -> str:
    if SLACK_WEBHOOK_URL:
        try:
            r = httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
            if r.status_code == 200 and r.text == "ok":
                print("[Slack] Enviado vÃ­a webhook â")
                return "webhook"
            print(f"[Slack] Webhook fallÃ³ ({r.status_code}), usando bot token...")
        except Exception as e:
            print(f"[Slack] Webhook error: {e}, usando bot token...")

    client = WebClient(token=SLACK_BOT_TOKEN)
    resp = client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=text)
    print(f"[Slack] Enviado vÃ­a bot token â â {resp['message']['ts']}")
    return resp["message"]["ts"]


# ââ Main âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

async def main():
    print(f"\n=== Propelbon Daily Brief â {TODAY} ===\n")
    print(f"Fuentes hoy: {SELECTED_NETWORKS + SELECTED_INDUSTRY + SELECTED_ECOMMERCE}\n")

    print("â PASO 0: Leyendo historial de Slack...")
    published_urls, published_topics = get_published_urls_and_topics()

    print("â PASO 1: Buscando noticias (Tavily + Twitter/X + LinkedIn + blogs)...")
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    search_results = search_news(tavily)
    source_texts = await fetch_all_sources()

    print("â PASO 2-4: Redactando briefing con Groq Llama 3.3 70B...")
    briefing = generate_briefing(search_results, source_texts, published_urls, published_topics)
    print("\n--- BRIEFING GENERADO ---")
    print(briefing)
    print("-------------------------\n")

    print("â PASO 5: Enviando a Slack...")
    send_to_slack(briefing)
    print("\nâ Briefing enviado correctamente.\n")


if __name__ == "__main__":
    asyncio.run(main())
