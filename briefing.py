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
    # Internacional / UK
    "https://hellopartner.com/tag/newsdesk/",
    "https://www.affiversemedia.com/news/",
    "https://performancein.com/news/",
    "https://www.affiliateinsider.com/category/news/",
    "https://digiday.com/tag/affiliate-marketing/",
    "https://econsultancy.com/topic/affiliates/",
    "https://influencermarketinghub.com/affiliate-marketing/",
    "https://www.clickz.com/category/affiliate-marketing/",
    "https://martech.org/topic/performance-marketing/",
    "https://www.thedrum.com/topic/performance-marketing",
    "https://www.accelerationpartners.com/resources/blog/",
    "https://www.affiliatesummit.com/blog/",
    # España
    "https://www.marketingdirecto.com/marketing-digital/",
    "https://iabspain.es/category/noticias/",
    "https://www.puromarketing.com/",
    "https://www.reasonwhy.es/",
    "https://marketing4ecommerce.net/",
]

# ── Fuentes: ecommerce ──────────────────────────────────────────────────────────
ECOMMERCE_SOURCES = [
    # Europa
    "https://www.ecommercenews.eu/news/",
    "https://retaildetail.eu/news/",
    "https://ecommerceeurope.eu/news/",
    "https://www.tamebay.com/",
    "https://www.modernretail.co/topic/ecommerce/",
    "https://www.retailgazette.co.uk/",
    "https://channelx.world",
    "https://internetretailing.net/",
    "https://www.retaildive.com/topic/e-commerce/",
    "https://www.digitalcommerce360.com/topic/ecommerce/",
    "https://practicalecommerce.com/",
    # España
    "https://www.ecommerce-news.es/",
    "https://www.inforetail.es/",
    # Plataformas
    "https://www.shopify.com/blog",
]

ALL_SOURCES = NETWORK_BLOGS + INDUSTRY_BLOGS + ECOMMERCE_SOURCES


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
            r = tavily.search(query=q, max_results=3, search_depth="advanced")
            results.extend(r.get("results", []))
        except Exception as e:
            print(f"[Tavily] Error en '{q}': {e}")

    print(f"[Tavily] {len(results)} resultados de búsqueda obtenidos")
    return results


async def fetch_source(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        # 120 chars por fuente — suficiente para capturar el primer titular
        return r.text[:120]
    except Exception as e:
        return ""  # silencioso: fuentes que fallen simplemente no aportan contenido


async def fetch_all_sources() -> dict[str, str]:
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (compatible; PropelbonBot/1.0)"}) as client:
        tasks = {url: fetch_source(client, url) for url in ALL_SOURCES}
        results = {}
        for url, coro in tasks.items():
            results[url] = await coro
    # Filtrar fuentes vacías (error o sin contenido útil)
    results = {url: text for url, text in results.items() if text.strip()}
    print(f"[HTTP] {len(results)}/{len(ALL_SOURCES)} fuentes con contenido")
    return results


# ── PASO 2-4: Groq (Llama 3.3 70B) redacta el briefing ──────────────────────

SYSTEM_PROMPT = """Eres el analista de inteligencia de mercado de Propelbon.

## Qué es Propelbon
Propelbon es una red de afiliación española, competidora directa de Awin, CJ, Tradedoubler, TradeTracker, impact.com, Partnerize, Webgains y Admitad. Opera exactamente igual que ellas: capta anunciantes (marcas/ecommerce), les monta y gestiona su programa de afiliación en modelo CPA (coste por adquisición), y conecta esos programas con publishers/afiliados que generan ventas.

Mercados principales: España y Portugal. En expansión: resto de Europa y LatAm.
Verticales: todos (moda, electrónica, viajes, finanzas, salud, hogar, etc.). 90% CPA.
Anunciantes: desde grandes marcas hasta pymes ecommerce.

## Objetivo de negocio de Propelbon
El objetivo central es ganar programas de afiliación. Esto ocurre de dos formas:
1. **Captación de nuevos anunciantes** — marcas que aún no tienen canal de afiliación y hay que convencerlas de abrirlo con Propelbon.
2. **Migración desde competidores** — marcas que ya tienen programa en Awin, CJ, TD, TradeTracker, etc., y hay que convencerlas de moverse a Propelbon o de abrir un programa adicional.

Las acciones concretas que el equipo puede ejecutar esta semana son:
- Llamar / prospectar a un tipo específico de anunciante
- Preparar un pitch para migrar programas de una red competidora
- Proponer un nuevo vertical o mercado a anunciantes actuales
- Ajustar estructura de comisiones o modelo de atribución
- Aprovechar debilidad o cambio de un competidor (subida de tarifas, problemas técnicos, cambio de política)
- Adaptar oferta a nueva regulación (cookies, GDPR, ePrivacy) antes que la competencia

## Cómo analizar cada noticia
Para cada noticia, razona internamente en este orden antes de escribir el "Para Propelbon:":
1. ¿Afecta a un vertical de anunciantes con los que trabajamos o podríamos trabajar?
2. ¿Crea una razón para llamar a un anunciante (nuevo problema, nueva oportunidad de canal)?
3. ¿Debilita o fortalece a algún competidor (Awin, CJ, TD, TT, etc.)?
4. ¿Cambia algo en cómo funciona el tracking, las cookies o la atribución (afecta a todos los programas)?
5. ¿Tiene impacto directo en España/Portugal o en los mercados de expansión?

El "Para Propelbon:" debe nombrar una acción específica, no una observación genérica.
MAL: "Oportunidad para nuestros anunciantes de expandirse a nuevos mercados."
BIEN: "Anunciantes de moda y electrónica en España que venden en marketplaces deberían plantearse abrir canal afiliación para compensar — prospección prioritaria esta semana."

## Señal del día
Elige la noticia con mayor impacto estratégico para Propelbon. La señal debe terminar siempre con:
"▶ Acción esta semana: [acción concreta, específica, ejecutable por el equipo de Propelbon]"
Ejemplos de acciones válidas: "Revisar qué anunciantes de [vertical] tenemos en [red competidora] y preparar propuesta de migración", "Llamar a los 5 anunciantes de viajes sin programa de afiliación activo en ES", "Actualizar propuesta de valor de Propelbon destacando [ventaja concreta frente a X]".

## Estilo
- Idioma: español (términos técnicos en inglés cuando son estándar del sector)
- Tono: directo, analítico, como un colega senior del equipo comercial. Sin fluff.
- Si la noticia viene de Twitter/X o LinkedIn, mencionarlo sutilmente.
- Formato de salida: mrkdwn de Slack (*negrita*, _cursiva_, <URL|texto>).

## Ámbito geográfico
Prioriza España, Portugal y Europa (UK, Francia, Alemania, Italia, DACH, Nordics). Excluye noticias exclusivamente del mercado US salvo que tengan impacto directo en Europa o en redes de afiliación globales.
"""

def build_user_prompt(search_results, source_texts, published_urls, published_topics, published_domains, topic_keywords):
    search_block = "\n\n".join([
        f"TÍTULO: {r.get('title','')}\nURL: {r.get('url','')}\nRESUMEN: {r.get('content','')[:100]}"
        for r in search_results
    ])
    sources_block = "\n\n---\n\n".join([
        f"FUENTE: {url}\n{text}"
        for url, text in source_texts.items()
    ])
    n_sources     = len(source_texts)
    published_list         = "\n".join(list(published_urls)[:60])
    published_topics_list  = "\n".join(published_topics[:40])
    published_domains_list = "\n".join(list(published_domains)[:40])
    topic_keywords_list    = "\n".join(topic_keywords[:40])

    return f"""Fecha de hoy: {TODAY}

=== URLS YA PUBLICADAS EN #noticias (NO repetir) ===
{published_list}

=== DOMINIOS YA USADOS EN BRIEFINGS ANTERIORES ===
{published_domains_list}

=== TITULARES YA PUBLICADOS (NO repetir temas sustancialmente iguales) ===
{published_topics_list}

=== KEYWORDS TEMÁTICAS YA CUBIERTAS (empresa + hecho clave) ===
{topic_keywords_list}

=== RESULTADOS DE BÚSQUMDA (incluye Twitter/X, LinkedIn, blogs) ===
{search_block}

=== CONTENIDO DE FUENTES DIRECTAS ({n_sources} fuentes consultadas) ===
Las fuentes llegan sin orden de importancia. Antes de redactar, evalúa cada una
y prioriza las que aporten noticias con mayor impacto real para Propelbon hoy.
Ignora fuentes con contenido genérico, evergreen o sin novedad en las últimas 72h.

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

*[Titular claro y directo]*
[2-3 frases: qué pasó, datos concretos, por qué importa en el sector]
💡 _Para Propelbon: [acción o implicación específica — qué vertical de anunciantes afecta, si es oportunidad de captación/migración, o si cambia algo operativo en los programas]_
🔗 <URL|Leer noticia>

[repetir por cada item ecommerce]

──────────────────────────
🤝 *AFILIACIÓN & PERFORMANCE*

*[Titular claro y directo]*
[2-3 frases: qué pasó, datos concretos, por qué importa en afiliación]
💡 _Para Propelbon: [acción específica — ¿debilita a un competidor?, ¿cambia el tracking o la atribución?, ¿es razón para llamar a un tipo de anunciante?]_
🔗 <URL|Leer noticia>

[repetir por cada item afiliación]

──────────────────────────
⚡ *SEÑAL DEL DÍA*
*[Titular del item de mayor impacto para el negocio de Propelbon]*
[2-3 frases de contexto ampliado con datos]
💡 _Por qué importa a Propelbon: [análisis específico — vertical afectado, competidor involucrado, mercado ES/PT/EU]_
▶ _Acción esta semana: [acción concreta y ejecutable por el equipo comercial de Propelbon]_
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
        model="llama-3.1-8b-instant",
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
                print("[Slack] Enviado via webhook ✓")
                return "webhook"
            print(f"[Slack] Webhook falló ({r.status_code}), usando bot token...")
        except Exception as e:
            print(f"[Slack] Webhook error: {e}, usando bot token...")

    client = WebClient(token=SLACK_BOT_TOKEN)
    resp = client.chat_postMessage(channel=SLACK_CHANNEL_ID, text=text)
    print(f"[Slack] Enviado via bot token ✓ ��� {resp['message']['ts']}")
    return resp["message"]["ts"]


# ── Main ────────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n=== Propelbon Daily Brief — {TODAY} ===\n")
    print(f"Fuentes configuradas: {len(ALL_SOURCES)} ({len(NETWORK_BLOGS)} redes + {len(INDUSTRY_BLOGS)} industria + {len(ECOMMERCE_SOURCES)} ecommerce)\n")

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
