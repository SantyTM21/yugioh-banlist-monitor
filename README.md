# Yu-Gi-Oh! Banlist Monitor

Monitor automático de la lista oficial **Forbidden & Limited** del Yu-Gi-Oh! TCG.

El proyecto consulta la página oficial de Konami, descubre automáticamente el
enlace de la lista vigente, compara las cartas con la última versión guardada y
puede enviar una alerta a Discord.

## Qué detecta

- Nueva lista publicada por Konami.
- Cambio de `Forbidden`, `Limited` o `Semi-Limited`.
- Carta que vuelve a estar `Unlimited` porque desaparece de la lista restringida.
- Cambio de fecha/URL/hash de la publicación oficial.
- Historial de snapshots en JSON.

La primera ejecución crea una línea base y **no envía una alerta** por defecto.

## Estructura

```text
yugioh-banlist-monitor/
├── .github/
│   └── workflows/
│       └── monitor-banlist.yml
├── data/
│   └── .gitkeep
├── tests/
│   └── test_monitor.py
├── .env.example
├── .gitignore
├── monitor.py
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## 1. Crear el repositorio en GitHub

Crea un repositorio nuevo, por ejemplo:

```text
yugioh-banlist-monitor
```

Sube todos los archivos de este proyecto.

## 2. Crear un webhook de Discord

En tu servidor de Discord:

1. Ve a **Ajustes del servidor**.
2. Abre **Integraciones**.
3. Entra en **Webhooks**.
4. Crea un webhook para el canal donde quieres recibir las alertas.
5. Copia la URL del webhook.

No publiques esa URL en el código ni en el repositorio.

## 3. Guardar el webhook como Secret de GitHub

En tu repositorio:

**Settings → Secrets and variables → Actions → New repository secret**

Nombre:

```text
DISCORD_WEBHOOK_URL
```

Valor: la URL completa del webhook de Discord.

El workflow ya lee ese secret automáticamente.

## 4. Ejecutarlo por primera vez

En GitHub:

**Actions → Monitor Yu-Gi-Oh Banlist → Run workflow**

La primera ejecución descargará la lista vigente y generará:

```text
data/current.json
data/history/AAAA-MM-DD.json
```

Después GitHub Actions hará commit de esos archivos.

La primera ejecución sirve como línea base y no manda una alerta, salvo que
configures `NOTIFY_ON_FIRST_RUN=true`.

## 5. Ejecución automática

El workflow incluye:

```yaml
schedule:
  - cron: "*/15 * * * *"
```

GitHub intentará ejecutar el monitor aproximadamente cada 15 minutos.

Los jobs programados de GitHub Actions pueden sufrir retrasos, así que no debe
considerarse un sistema de tiempo real al segundo.

## Ejecutarlo localmente

Python 3.11+ recomendado.

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python monitor.py
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q
python monitor.py
```

Para probar Discord localmente:

```bash
export DISCORD_WEBHOOK_URL="TU_WEBHOOK"
python monitor.py
```

En PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL="TU_WEBHOOK"
python monitor.py
```

## Cómo funciona

1. Visita:
   `https://www.yugioh-card.com/lat-am/limited/`
2. Busca el enlace fechado a la lista vigente.
3. Descarga esa lista.
4. Extrae las filas de Advanced Format.
5. Guarda cada carta con su estado.
6. Compara el nuevo snapshot con `data/current.json`.
7. Si hay cambios, envía Discord.
8. Guarda el nuevo snapshot e historial.

## Ejemplo de alerta

```text
🚨 ¡La lista de Prohibidas y Limitadas de Yu-Gi-Oh! cambió!

Vigencia: 2026-XX-XX

Cambios detectados:
🔴 CARTA A: Limited → Forbidden
🟡 CARTA B: Unlimited → Limited
🟢 CARTA C: Forbidden → Unlimited

Fuente oficial: https://www.yugioh-card.com/...
```

## Seguridad

`DISCORD_WEBHOOK_URL` funciona como una credencial. Si alguien obtiene la URL,
puede publicar mensajes usando ese webhook.

- Guárdala únicamente como GitHub Secret.
- No la pongas en `.env` y luego subas `.env` a GitHub.
- Si se filtra, elimina/regenera el webhook en Discord.

## Si Konami cambia el HTML

El monitor falla de forma intencional si deja de encontrar la lista o no puede
extraer cartas. De esa manera evita reemplazar el estado correcto por datos
vacíos y esconder un problema.

Revisa el log de GitHub Actions si el job aparece en rojo.
