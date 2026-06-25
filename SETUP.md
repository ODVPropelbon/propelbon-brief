# Propelbon Daily Brief — Setup

Script autónomo que genera y envía el briefing diario a #noticias **sin Cowork, sin Anthropic, sin coste**. Funciona aunque el ordenador esté apagado.

## Requisitos previos

- Cuenta en GitHub (gratis)
- API key de Google Gemini → https://aistudio.google.com/apikey (gratis — 1.500 req/día)
- API key de Tavily → https://app.tavily.com (plan gratuito: 1.000 búsquedas/mes)
- Slack Bot Token con permisos `channels:history` y `chat:write`

---

## 1. Crear el Slack Bot

1. Ve a https://api.slack.com/apps → **Create New App** → **From scratch**
2. Nombre: `Propelbon Radar`, workspace: Propelbon
3. En **OAuth & Permissions** → **Bot Token Scopes**, añade:
   - `channels:history`
   - `chat:write`
4. Haz clic en **Install to Workspace**
5. Copia el **Bot User OAuth Token** (empieza por `xoxb-...`) → será tu `SLACK_BOT_TOKEN`
6. Invita el bot al canal: en #noticias escribe `/invite @Propelbon Radar`

> **Opcional**: si quieres mantener la identidad del bot "Propelbon Radar" con avatar:
> En **Incoming Webhooks** actívalo y copia la URL → será `SLACK_WEBHOOK_URL`.

---

## 2. Obtener la API key de Gemini (gratis)

1. Ve a https://aistudio.google.com/apikey
2. Haz clic en **Create API key**
3. Copia la key → será tu `GEMINI_API_KEY`

No requiere tarjeta de crédito. El plan gratuito incluye 1.500 peticiones/día con Gemini 2.0 Flash.

---

## 3. Crear el repositorio en GitHub

```bash
# En tu terminal local:
mkdir propelbon-brief && cd propelbon-brief
git init
# Copia aquí los archivos: briefing.py, requirements.txt, .github/workflows/daily-brief.yml
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/TU_USUARIO/propelbon-brief.git
git push -u origin main
```

---

## 4. Añadir los secrets en GitHub

En tu repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret               | Valor                                      |
|----------------------|--------------------------------------------|
| `GEMINI_API_KEY`     | AIza... (de Google AI Studio)              |
| `SLACK_BOT_TOKEN`    | xoxb-... (Bot User OAuth Token)            |
| `SLACK_CHANNEL_ID`   | C0B93TX9SQL                                |
| `SLACK_WEBHOOK_URL`  | https://hooks.slack.com/... (opcional)     |
| `TAVILY_API_KEY`     | tvly-...                                   |

---

## 5. Verificar que funciona

En GitHub → **Actions** → selecciona el workflow **Propelbon Daily Brief** → **Run workflow** (botón manual).

Si todo va bien, verás el briefing en #noticias en ~1-2 minutos.

---

## 6. Horario

El workflow está configurado para ejecutarse a las **08:00 hora Madrid** de lunes a viernes.

GitHub Actions usa UTC. El archivo `.github/workflows/daily-brief.yml` tiene `cron: '0 6 * * 1-5'` (06:00 UTC = 08:00 CEST en verano). En invierno (CET, UTC+1) cámbialo a `0 7 * * 1-5` para mantener las 08:00.

---

## Costes

| Servicio           | Coste                      |
|--------------------|---------------------------|
| GitHub Actions     | Gratis (2.000 min/mes)    |
| Google Gemini Flash| **Gratis** (1.500 req/día)|
| Tavily             | Gratis (1.000 búsq./mes)  |
| Slack              | Gratis                     |

**Total: 0€/mes.**

---

## Ejecutar localmente (para probar)

```bash
pip install -r requirements.txt

export GEMINI_API_KEY="AIza..."
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_CHANNEL_ID="C0B93TX9SQL"
export TAVILY_API_KEY="tvly-..."

python briefing.py
```
