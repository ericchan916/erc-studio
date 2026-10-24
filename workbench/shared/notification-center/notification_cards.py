#!/usr/bin/env python3
"""CardKit card building and validation; plain text is never a valid payload."""
from __future__ import annotations

from typing import Any, Iterable


class CardInvalid(ValueError):
    """Raised when a card payload is not a valid interactive CardKit card."""


def render_mentions(mentions: Iterable[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for item in mentions or []:
        if not isinstance(item, dict) or not str(item.get("id") or ""):
            raise CardInvalid("mention_id_required")
        parts.append(f'<at id="{item["id"]}"></at>')
    return " ".join(parts)


def build_card(
    title: str,
    lines: Iterable[str] | None = None,
    mentions: Iterable[dict[str, Any]] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if not str(title or "").strip():
        raise CardInvalid("title_required")
    content = [f"## {title}"]
    mention_line = render_mentions(mentions)
    if mention_line:
        content.append(mention_line)
    if note:
        content.append(f"> {note}")
    content.extend(str(line) for line in lines or [])
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "body": {"elements": [{"tag": "markdown", "content": "\n".join(content)}]},
    }
    validate_card(card)
    return card


def validate_card(card: Any) -> None:
    if not isinstance(card, dict):
        raise CardInvalid("card_object_required")
    if card.get("schema") is None:
        _validate_card1_action(card)
        return
    if card.get("schema") != "2.0":
        raise CardInvalid("cardkit_schema_2_required")
    config = card.get("config")
    if config is not None and not isinstance(config, dict):
        raise CardInvalid("card_config_invalid")
    body = card.get("body")
    if not isinstance(body, dict):
        raise CardInvalid("card_body_required")
    elements = body.get("elements")
    if not isinstance(elements, list) or not elements:
        raise CardInvalid("card_elements_required")
    for element in elements:
        if not isinstance(element, dict) or not element.get("tag"):
            raise CardInvalid("card_element_tag_required")


def _validate_card1_action(card: dict[str, Any]) -> None:
    config = card.get("config")
    if config is not None and not isinstance(config, dict):
        raise CardInvalid("card_config_invalid")
    elements = card.get("elements")
    if not isinstance(elements, list) or not elements:
        raise CardInvalid("card1_elements_required")
    action_elements = [item for item in elements if isinstance(item, dict) and item.get("tag") == "action"]
    if not action_elements:
        raise CardInvalid("card1_action_required")
    for element in elements:
        if not isinstance(element, dict) or not element.get("tag"):
            raise CardInvalid("card_element_tag_required")
    for action_element in action_elements:
        actions = action_element.get("actions")
        if not isinstance(actions, list) or not actions:
            raise CardInvalid("card1_buttons_required")
        for button in actions:
            if not isinstance(button, dict) or button.get("tag") != "button":
                raise CardInvalid("card1_button_invalid")
            if not isinstance(button.get("value"), dict) or not button["value"]:
                raise CardInvalid("card1_button_value_required")
            if any(key in button for key in ("url", "multi_url", "behaviors")):
                raise CardInvalid("card1_button_navigation_forbidden")
