"""
Propelbon Daily Brief — script autónomo (stack 100% gratuito)
Secrets necesarios (env vars o GitHub Secrets):
  GROQ_API_KEY         (Groq — gratis: console.groq.com, modelo llama-3.3-70b)
  SLACK_BOT_TOKEN      (token del bot con permisos channels:history + chat:write)
  SLACK_CHANNEL_ID     (C0B93TX9SQL)
  SLACK_WEBHOOK_URL    (opcional)
  TAVILY_API_KEY       (Tavily free tier: 1.000 búsquedas/mes gratis)
"""

import os
import asyncio
import httpx
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from groq import Groq
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from tavily import TavilyClient


# ── Configuración ──────────────────────────────────────────────────────────────

GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
SLACK_BOT_TOKEN   = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID  = os.environ.get("SLACK_CHANNEL_ID", "C0B93TX9SQL")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
TAVILY_API_KEY    = os.environ["TAVILY_API_KEY"]

MADRID_TZ = ZoneInfo("Europe/Madrid")
TODAY = datetime.now(MADRID_TZ).strftime("%d %B %Y")
TODAY_SHORT = datetime.now(MADRID_TZ).strftime("%-d %b %Y")

# ── Fuentes: blogs de redes de afiliación ──────────────────────────────────────
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

# ── Fuentes: blogs especializados EN + ES ──────────────────────────────────────
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

# ── Fuentes: ecommerce ──────────────────────────────────────────────────────────
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

# Rotar fuentes por día (0=lunes): seleccionar 3 de cada pool
_dow = datetime.now(MADRID_TZ).weekday()
def _rotate(pool, n=3):
    start = (_dow * n) % len(pool)
    return (pool + pool)[start:start + n]

SELECTED_NETWORKS  = _rotate(NETWORK_BLOGS, 3)
SELECTED_INDUSTRY  = _rotate(INDUSTRY_BLOGS, 3)
SELECTED_ECOMMERCE = _rotate(ECOMMERCE_SOURCES, 3)


# ── PASO 0: Leer historial de Slack ────────────────────────────────────────────

def get_published_urls_and_topics(limit_messages: int = 300) -> tuple[set[str], list[str], set[str], list[str]]:
    """
    Devuelve:
    - urls: todas las URLs publicadas en el canal
    - topics: todos los titulares publicados
    - domains: todos los dominios ya usados
    - topic_keywords: palabras clave de empresa/hecho extraídas de los titulares
    """
    client = WebClient(token=SLACK_BOT_TOKEN)
    urls: set[str] = set()
    topics: list[str] = []
    domains: set[str] = set()
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
                try:
                    domains.add(urlparse(url).netloc)
                except Exception:
                    pass
            for title in re.findall(r"\*([^*]{10,120})\*", text):
                topics.append(title.strip())

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor or len(urls) > limit_messages:
            break

    # Extraer keywords temáticas: empresa + acción clave de cada titular
    # Para ayudar al LLM a detectar el mismo hecho con distintas URLs
    topic_keywords = _extract_topic_keywords(topics)

    print(f"[Slack] {len(urls)} URLs · {len(topics)} titulares · {len(domains)} dominios · {len(topic_keywords)} keywords temáticas")
    return urls, topics, domains, topic_keywords


def _extract_topic_keywords(topics: list[str]) -> list[str]:
    """
    De cada titular extrae una frase corta empresa+hecho para
    detectar duplicados temáticos aunque la URL o fuente sea distinta.
    Ejemplo: "TikTok Shop se expande a Europa" → "tiktok shop europa expansion"
    """
    keywords = []
    for t in topics[:80]:
        # Normalizar: minúsculas, quitar símbolos, quedarse con palabras sustantivas
        cleaned = re.sub(r"[^\w\s]", " ", t.lower())
        words = cleaned.split()
        # Filtrar stopwords básicas
        stopwords = {"el","la","los","las","de","del","en","un","una","y","a","que",
                     "se","su","por","para","con","es","al","the","a","an","of","in",
                     "and","to","for","is","it","on","at","by","as","are","was","has"}
        meaningful = [w for w in words if len(w) > 3 and w not in stopwords]
        if len(meaningful) >= 2:
            keywords.append(" ".join(meaningful[:6]))
    return keywords


# ── PASO 1: Búsquedas de noticias ──────────────────────────────────────────────

def search_news(tavily: TavilyClient) -> list[dict]:
    queries = [
        # Noticias generales ecommerce
        f"ecommerce news Europe Spain UK {TODAY}",
        f"ecommerce España noticias {TODAY}",
        # Noticias de afiliación
        f"affiliate marketing news {TODAY}",
        f"performance marketing news {TODAY}",
        # Noticias específicas de redes
        f"Awin Tradedoubler impact.com Partnerize TradeTracker affiliate network news Europe {TODAY}",
        f"Admitad Webgains TradeTracker CJ affiliate network news {TODAY}",
        # Social: Twitter/X
        f"site:x.com OR site:twitter.com affiliate marketing awin tradedoubler impact partnerize {TODAY}",
        f"site:x.com OR site:twitter.com ecommerce performance marketing {TODAY}",
        # LinkedIn
        f"site:linkedin.com affiliate marketing performance ecommerce {TODAY}",
        # Tendencias (genérico, no ancla a una noticia concreta)
        f"ecommerce platforms Europe new markets expansion {TODAY}",
        f"cookie tracking privacy affiliate marketing {TODAY}",
    ]

    results = []
    for q in queries:
        try:
            r = tavily.search(query=q, max_results=4, search_depth="advanced")
            results.extend(r.get("results", []))
        except Exception as e:
            print(f"[Tavily] Error en '{q}': {e}")

    print(f"[Tavily] {len(results)} resultados de búsqueda obtenidos")
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


# ── PASO 2-4: Groq (Llama 3.3 70B) redacta el briefing ──────────────────────

SYSTEM_PROMPT = """Eres el asistente de noticias de Propelbon, empresa española de marketing de afiliación y performance que trabaja con anunciantes ecommerce en España y Europa.

Tu tarea: redactar el briefing diario de noticias para el canal #noticias de Slack.

ÁMBITO GEOGRÁFICO: Prioriza noticias de España, Europa (UK, Francia, Alemania, Italia, DACH, Nordics) y mercados globales con impacto en Europa. Excluye o da muy poco peso a noticias exclusivamente del mercado estadounidense salvo que tengan impacto directo en Europa o en las redes de afiliación globales.

Fuentes que se consultan cada día: blogs oficiales de redes de afiliación (Awin, Tradedoubler, Admitad, impact.com, Partnerize, Webgains, TradeTracker, CJ), blogs especializados (PerformanceIN, Hello Partner, Affiverse, MarTech, Marketing Directo, IAB Spain), Twitter/X y LinkedIn de estas redes, y medios de ecommerce (eCommerce News, Retail Dive, Digital Commerce 360, etc.).

Estilo:
- Idioma: español (términos técnicos en inglés cuando son estándar del sector)
- Tono: directo, analítico, sin fluff. Como un colega senior del sector.
- Perspectiva siempre desde Propelbon: ¿qué significa esto para nuestros anunciantes o publishers?
- Si la noticia viene de Twitter/X o LinkedIn, mencionarlo sutilmente (ej: "según publica en X...")

Formato de salida: mrkdwn de Slack (usar *negrita*, _cursiva_, <URL|texto>).
"""

def build_user_prompt(search_results, source_texts, published_urls, published_topics, published_domains, topic_keywords):
    search_block = "\n\n".join([
        f"TÍTULO: {r.get('title','')}\nURL: {r.get('url','')}\nRESUMEN: {r.get('content','')[:200]}"
        for r in search_results
    ])
    sources_block = "\n\n---\n\n".join([
        f"FUENTE: {url}\n{text[:600]}"
        for url, text in source_texts.items()
    ])
    published_list         = "\n".join(list(published_urls)[:80])
    published_topics_list  = "\n".join(published_topics[:60])
    published_domains_list = "\n".join(list(published_domains)[:60])
    topic_keywords_list    = "\n".join(topic_keywords[:60])

    return f"""Fecha de hoy: {TODAY}

=== URLS YA PUBLICADAS EN #noticias (NO repetir) ===
{published_list}

=== DOMINIOS YA USADOS EN BRIEFINGS ANTERIORES ===
{published_domains_list}

=== TITULARES YA PUBLICADOS (NO repetir temas sustancialmente iguales) ===
{published_topics_list}

=== KEYWORDS TEMÁTICAS YA CUBIERTAS (empresa + hecho clave) ===
{topic_keywords_list}

=== RESULTADOS DE BÚSQUEDA (incluye Twitter/X, LinkedIn, blogs) ===
{search_block}

=== CONTENIDO DE FUENTES DIRECTAS (blogs de redes + industria + ecommerce) ===
{sources_block}

---

Redacta el briefing diario siguiendo estas reglas ESTRICTAS:

1. FILTRO DE DUPLICADOS (aplicar en este orden):
   a) URL exacta: si la URL ya está en la lista de URLs publicadas → DESCARTAR
   b) Tema idéntico: si el hecho que describe la noticia ya está cubierto en los titulares o keywords anteriores → DESCARTAR, aunque la URL sea distinta y aunque la fuente sea diferente
      - Ejemplo: "TikTok Shop expands to Europe" / "TikTok Shop se expande a Austria" / "TikTok Shop launches in Poland" son el MISMO hecho → si aparece en el historial, DESCARTAR toda variante
      - Ejemplo: "Awin affiliate trends report 2026" ya publicado → DESCARTAR cualquier otra cobertura del mismo informe
   c) Dominio repetido: si el dominio ya está en la lista de dominios usados, solo incluirlo si la noticia es claramente distinta a cualquier otra ya publicada de ese dominio
   d) Si tras aplicar los filtros no quedan suficientes noticias nuevas, escribe en esa sección: "_Poco movimiento hoy en este área. La próxima actualización llegará mañana._" — nunca repitas noticias ya publicadas.

2. SELECCIÓN (solo noticias que pasen el filtro):
   - ECOMMERCE: 2-4 items. Priorizar España/Europa: regulación EU, grandes retailers europeos, plataformas con impacto en Europa. Descartar noticias exclusivas del mercado US salvo impacto global.
   - AFILIACIÓN & PERFORMANCE: 2-4 items. Priorizar noticias con impacto en Europa: redes (Awin, Tradedoubler, TradeTracker, impact.com, Partnerize, Webgains, CJ), normativa ePrivacy/GDPR, tracking, nuevos programas en España/EU.
   - Máximo 2 items del mismo dominio por briefing.
   - Solo noticias de las últimas 72h salvo que sean de alto impacto y no hayan sido cubiertas nunca.

3. FORMATO DE SALIDA (mrkdwn exacto, sin texto adicional):

📰 *Propelbon Daily Brief · {TODAY_SHORT}*
_[N fuentes consultadas · redes: Awin, TD, Admitad, impact, Partnerize, Webgains, CJ, TT]_

──────────────────────────
📦 *ECOMMERCE*

*[Titular noticia 1]*
[2-3 frases resumen con datos concretos]
💡 _Para Propelbon: [implicación concreta]_
🔗 <URL|Leer noticia>

[repetir por cada item ecommerce]

──────────────────────────
🤝 *AFILIACIÓN & PERFORMANCE*

[items igual]

──────────────────────────
⚡ *SEÑAL DEL DÍA*
*[Titular del item de mayor impacto estratégico]*
[Contexto ampliado + acción concreta para esta semana]
🔗 <URL|Leer noticia>

IMPORTANTE: devuelve SOLO el mensaje mrkdwn, sin texto previo ni posterior.
"""


def generate_briefing(search_results, source_texts, published_urls, published_topics, published_domains, topic_keywords):
    client = Groq(api_key=GROQ_API_KEY)
    user_prompt = build_user_prompt(
        search_results, source_texts,
        published_urls, published_topics,
        published_domains, topic_keywords
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=2500,
    )
    return response.choices[0].message.content.strip()


# ── PASO 5: Enviar a Slack ──────────────────────────────────────────────────────

def send_to_slack(text: str) -> str:
    if SLACK_WEBHOOK_URL:
        try:
            r = httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
            if r.status_code == 200 and r.text == "ok":
                print("[Slack] Enviado vía webhook ✓")
                return "webhook"
            print(f"[Slack] Webhook falló ({r.status_code}), usando bot token...")
        except Exception as e:
            print(f"[Slack] Webhook error: {e}, usando bot token...")

    client = WebClient(token=SLACK_BOT_TOKEN)
    resp = client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=text)
    print(f"[Slack] Enviado vía bot token ✓ → {resp['message']['ts']}")
    return resp["message"]["ts"]


# ── Main ────────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n=== Propelbon Daily Brief — {TODAY} ===\n")
    print(f"Fuentes hoy: {SELECTED_NETWORKS + SELECTED_INDUSTRY + SELECTED_ECOMMERCE}\n")

    print("▸ PASO 0: Leyendo historial de Slack...")
    published_urls, published_topics, published_domains, topic_keywords = get_published_urls_and_topics()

    print("▸ PASO 1: Buscando noticias (Tavily + Twitter/X + LinkedIn + blogs)...")
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    search_results = search_news(tavily)
    source_texts = await fetch_all_sources()

    print("▸ PASO 2-4: Redactando briefing con Groq Llama 3.3 70B...")
    briefing = generate_briefing(
        search_results, source_texts,
        published_urls, published_topics,
        published_domains, topic_keywords
    )
    print("\n--- BRIEFING GENERADO ---")
    print(briefing)
    print("-------------------------\n")

    print("▸ PASO 5: Enviando a Slack...")
    send_to_slack(briefing)
    print("\n✓ Briefing enviado correctamente.\n")


if __name__ == "__main__":
    asyncio.run(main())
