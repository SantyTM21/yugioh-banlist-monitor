from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LANDING_URL = os.getenv(
    "YUGIOH_LIMITED_LANDING_URL",
    "https://www.yugioh-card.com/lat-am/limited/",
)
STATE_FILE = Path(os.getenv("STATE_FILE", "data/current.json"))
HISTORY_DIR = Path(os.getenv("HISTORY_DIR", "data/history"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
NOTIFY_ON_FIRST_RUN = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() == "true"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

USER_AGENT = (
    "YuGiOhBanlistMonitor/1.0 "
    "(personal change monitor; contact via repository issues)"
)

STATUS_MAP = {
    "forbidden": "Forbidden",
    "prohibida": "Forbidden",
    "prohibido": "Forbidden",
    "limited": "Limited",
    "limitada": "Limited",
    "limitado": "Limited",
    "semi-limited": "Semi-Limited",
    "semi limited": "Semi-Limited",
    "semilimited": "Semi-Limited",
    "semi-limitada": "Semi-Limited",
    "semi limitada": "Semi-Limited",
    "semilimitada": "Semi-Limited",
    "semi-limitado": "Semi-Limited",
    "semi limitado": "Semi-Limited",
    "semilimitado": "Semi-Limited",
}

STATUS_EMOJI = {
    "Forbidden": "🔴",
    "Limited": "🟡",
    "Semi-Limited": "🟠",
    "Unlimited": "🟢",
}

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
    }
)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def normalize_for_match(value: str) -> str:
    value = normalize_space(value).lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def get(url: str) -> requests.Response:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def discover_current_list_url() -> str:
    """Find the dated Forbidden/Limited list linked from Konami's landing page."""
    response = get(LANDING_URL)
    soup = BeautifulSoup(response.text, "html.parser")

    candidates: list[tuple[str, str]] = []
    pattern = re.compile(r"/limited/list_(\d{4}-\d{2}-\d{2})/?", re.I)

    for anchor in soup.find_all("a", href=True):
        href = urljoin(response.url, anchor["href"])
        match = pattern.search(href)
        if match:
            candidates.append((match.group(1), href))

    if not candidates:
        raise RuntimeError(
            "No se encontró un enlace fechado a la lista vigente en "
            f"{response.url}. Es posible que Konami haya cambiado la estructura."
        )

    # Usually there is only one current-list link. Choosing the greatest ISO date
    # also protects us if the page temporarily contains more than one.
    _, current_url = max(candidates, key=lambda item: item[0])
    return current_url


def canonical_status(value: str) -> str | None:
    key = normalize_for_match(value).replace("–", "-").replace("—", "-")
    key = re.sub(r"\s*-\s*", "-", key)
    return STATUS_MAP.get(key)


def extract_dates(soup: BeautifulSoup, list_url: str) -> tuple[str | None, str | None]:
    text = normalize_space(soup.get_text(" ", strip=True))

    effective = None
    updated = None

    effective_patterns = [
        r"Efectivo desde el\s+([^|]+?)(?=\s+(?:Las cartas|The next|Actualización|Actualizacion|$))",
        r"Effective from\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    ]
    for pattern in effective_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            effective = normalize_space(match.group(1))
            break

    update_match = re.search(
        r"(?:Actualizaci[oó]n|Updated)\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        text,
        re.I,
    )
    if update_match:
        updated = update_match.group(1)

    if not effective:
        url_date = re.search(r"list_(\d{4})-(\d{2})-(\d{2})", list_url)
        if url_date:
            year, month, day = url_date.groups()
            effective = f"{year}-{month}-{day}"

    return effective, updated


def parse_cards(soup: BeautifulSoup) -> list[dict[str, str]]:
    """
    Parse Advanced Format rows.

    Konami currently publishes rows like:
    Card Type | Card Name | Advanced Format | Traditional Format | Remarks

    We deliberately find the first recognized restriction status in each row,
    which corresponds to Advanced Format on the official TCG list.
    """
    cards: dict[str, dict[str, str]] = {}

    for row in soup.find_all("tr"):
        cells = [
            normalize_space(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]
        if len(cells) < 3:
            continue

        status_index = None
        status = None
        for index, cell in enumerate(cells):
            candidate = canonical_status(cell)
            if candidate:
                status_index = index
                status = candidate
                break

        if status_index is None or status_index < 1 or status is None:
            continue

        name = cells[status_index - 1]
        if not name or normalize_for_match(name) in {
            "card name",
            "nombre de la carta",
        }:
            continue

        card_type = cells[status_index - 2] if status_index >= 2 else ""
        # In the current page, Advanced is followed by Traditional and Remarks.
        remarks = cells[status_index + 2] if len(cells) > status_index + 2 else ""

        cards[name] = {
            "name": name,
            "status": status,
            "type": card_type,
            "remarks": remarks,
        }

    if not cards:
        raise RuntimeError(
            "No se pudieron extraer cartas de la lista. "
            "Konami podría haber cambiado el HTML."
        )

    return sorted(cards.values(), key=lambda card: card["name"].casefold())


def make_content_hash(cards: list[dict[str, str]]) -> str:
    canonical = json.dumps(
        cards,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch_snapshot() -> dict[str, Any]:
    list_url = discover_current_list_url()
    response = get(list_url)
    soup = BeautifulSoup(response.text, "html.parser")

    cards = parse_cards(soup)
    effective_date, updated_date = extract_dates(soup, response.url)

    return {
        "source": response.url,
        "effective_date": effective_date,
        "updated_date": updated_date,
        "content_hash": make_content_hash(cards),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cards": cards,
    }


def load_state() -> dict[str, Any] | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def safe_history_name(snapshot: dict[str, Any]) -> str:
    url_match = re.search(r"list_(\d{4}-\d{2}-\d{2})", snapshot["source"])
    if url_match:
        return url_match.group(1)

    effective = snapshot.get("effective_date") or "unknown-date"
    effective = re.sub(r"[^0-9A-Za-z_-]+", "-", effective).strip("-")
    return effective or "unknown-date"


def save_snapshot(snapshot: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    STATE_FILE.write_text(payload, encoding="utf-8")

    history_file = HISTORY_DIR / f"{safe_history_name(snapshot)}.json"
    history_file.write_text(payload, encoding="utf-8")


def compare_snapshots(
    old: dict[str, Any], new: dict[str, Any]
) -> list[dict[str, str]]:
    old_cards = {card["name"]: card for card in old.get("cards", [])}
    new_cards = {card["name"]: card for card in new.get("cards", [])}

    changes: list[dict[str, str]] = []

    for name in sorted(set(old_cards) | set(new_cards), key=str.casefold):
        old_card = old_cards.get(name)
        new_card = new_cards.get(name)

        old_status = old_card["status"] if old_card else "Unlimited"
        new_status = new_card["status"] if new_card else "Unlimited"

        if old_status != new_status:
            changes.append(
                {
                    "name": name,
                    "from": old_status,
                    "to": new_status,
                }
            )

    return changes


def snapshot_changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    return any(
        [
            old.get("source") != new.get("source"),
            old.get("effective_date") != new.get("effective_date"),
            old.get("updated_date") != new.get("updated_date"),
            old.get("content_hash") != new.get("content_hash"),
        ]
    )


def format_change(change: dict[str, str]) -> str:
    icon = STATUS_EMOJI.get(change["to"], "•")
    return f'{icon} **{change["name"]}**: {change["from"]} → {change["to"]}'


def build_discord_messages(
    old: dict[str, Any] | None,
    new: dict[str, Any],
    changes: list[dict[str, str]],
    first_run: bool = False,
) -> list[str]:
    if first_run:
        title = "✅ Monitor de Banlist Yu-Gi-Oh! inicializado"
        body = (
            "Se guardó la lista vigente como línea base. "
            "Las próximas modificaciones generarán una alerta."
        )
    else:
        title = "🚨 ¡La lista de Prohibidas y Limitadas de Yu-Gi-Oh! cambió!"
        body = (
            f"**Vigencia:** {new.get('effective_date') or 'no detectada'}\n"
            f"**Actualización del sitio:** {new.get('updated_date') or 'no detectada'}"
        )

    header = f"{title}\n\n{body}\n\n"
    footer = f"\nFuente oficial: {new['source']}"

    lines = [format_change(change) for change in changes]
    if not lines and not first_run:
        lines = [
            "La publicación oficial cambió, pero no se detectó un cambio "
            "de estado entre las cartas extraídas."
        ]

    # Discord content messages are capped at 2000 characters.
    max_len = 1900
    messages: list[str] = []
    current = header

    if lines:
        current += "**Cambios detectados:**\n"

    for line in lines:
        candidate = current + line + "\n"
        if len(candidate + footer) > max_len and current.strip():
            messages.append(current.rstrip())
            current = ""
        current += line + "\n"

    current = current.rstrip() + footer
    if len(current) <= max_len:
        messages.append(current)
    else:
        # Very defensive fallback for unusually long card names / URLs.
        messages.extend(
            current[i : i + max_len] for i in range(0, len(current), max_len)
        )

    return messages


def send_discord(messages: list[str]) -> None:
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL no configurado; se omite la notificación.")
        return

    for message in messages:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "content": message,
                "allowed_mentions": {"parse": []},
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()


def print_summary(snapshot: dict[str, Any]) -> None:
    counts = {"Forbidden": 0, "Limited": 0, "Semi-Limited": 0}
    for card in snapshot["cards"]:
        counts[card["status"]] = counts.get(card["status"], 0) + 1

    print(f"Fuente: {snapshot['source']}")
    print(f"Vigencia: {snapshot.get('effective_date')}")
    print(f"Actualización: {snapshot.get('updated_date')}")
    print(
        "Cartas: "
        f"{counts.get('Forbidden', 0)} prohibidas, "
        f"{counts.get('Limited', 0)} limitadas, "
        f"{counts.get('Semi-Limited', 0)} semilimitadas"
    )


def main() -> int:
    try:
        new = fetch_snapshot()
        old = load_state()
        print_summary(new)

        if old is None:
            print("Primera ejecución: guardando línea base.")
            save_snapshot(new)
            if NOTIFY_ON_FIRST_RUN:
                send_discord(
                    build_discord_messages(
                        old=None,
                        new=new,
                        changes=[],
                        first_run=True,
                    )
                )
            return 0

        if not snapshot_changed(old, new):
            print("Sin cambios.")
            return 0

        changes = compare_snapshots(old, new)
        print(f"Cambio detectado. Cambios de estado: {len(changes)}")
        for change in changes:
            print(
                f"- {change['name']}: "
                f"{change['from']} -> {change['to']}"
            )

        # Notify before persisting. If Discord fails, the action fails and the
        # previous state remains, so a later run can retry the notification.
        send_discord(build_discord_messages(old, new, changes))
        save_snapshot(new)
        print("Nuevo estado guardado.")
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
