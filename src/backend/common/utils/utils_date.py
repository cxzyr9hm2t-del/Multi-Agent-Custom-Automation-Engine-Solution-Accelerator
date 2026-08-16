import json
import locale
import logging
from datetime import datetime, timezone
from typing import Optional

import regex as re
from dateutil import parser


def utc_now_iso() -> str:
    """Return the current instant as an ISO-8601 string in UTC.

    Every timestamp the backend sends to the browser should come from here.
    Two other things had been used for the job and neither survived the trip:

    * ``time.time()`` produces epoch **seconds**, while JavaScript's ``Date``
      constructor reads a bare number as epoch **milliseconds**. A message
      stamped that way rendered as January 1970.
    * ``asyncio.get_event_loop().time()`` is a **monotonic** clock counting
      from an arbitrary origin. It is the right tool for measuring a duration
      and meaningless as a wall-clock instant.

    An ISO-8601 string with an explicit offset is unambiguous in transit and
    is parsed correctly by ``new Date(...)``, which then renders it in the
    viewer's own time zone.
    """
    return datetime.now(timezone.utc).isoformat()


def format_date_for_user(date_str: str, user_locale: Optional[str] = None) -> str:
    """
    Format date based on user's desktop locale preference.

    Args:
        date_str (str): Date in ISO format (YYYY-MM-DD).
        user_locale (str, optional): User's locale string, e.g., 'en_US', 'en_GB'.

    Returns:
        str: Formatted date respecting locale or raw date if formatting fails.
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        locale.setlocale(locale.LC_TIME, user_locale or "")
        return date_obj.strftime("%B %d, %Y")
    except Exception as e:
        logging.warning(f"Date formatting failed for '{date_str}': {e}")
        return date_str


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for handling datetime objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def format_dates_in_messages(messages, target_locale="en-US"):
    """
    Format dates in agent messages according to the specified locale.

    Args:
        messages: List of message objects or string content
        target_locale: Target locale for date formatting (default: en-US)

    Returns:
        Formatted messages with dates converted to target locale format
    """
    # Define target format patterns per locale
    locale_date_formats = {
        "en-IN": "%d %b %Y",  # 30 Jul 2025
        "en-US": "%b %d, %Y",  # Jul 30, 2025
    }

    output_format = locale_date_formats.get(target_locale, "%d %b %Y")
    # Match both "Jul 30, 2025, 12:00:00 AM" and "30 Jul 2025"
    date_pattern = r"(\d{1,2} [A-Za-z]{3,9} \d{4}|[A-Za-z]{3,9} \d{1,2}, \d{4}(, \d{1,2}:\d{2}:\d{2} ?[APap][Mm])?)"

    def convert_date(match):
        date_str = match.group(0)
        try:
            dt = parser.parse(date_str)
            return dt.strftime(output_format)
        except Exception:
            return date_str  # Leave it unchanged if parsing fails

    # Process messages
    if isinstance(messages, list):
        formatted_messages = []
        for message in messages:
            if hasattr(message, "content") and message.content:
                # Create a copy of the message with formatted content
                formatted_message = (
                    message.model_copy() if hasattr(message, "model_copy") else message
                )
                if hasattr(formatted_message, "content"):
                    formatted_message.content = re.sub(
                        date_pattern, convert_date, formatted_message.content
                    )
                formatted_messages.append(formatted_message)
            else:
                formatted_messages.append(message)
        return formatted_messages
    elif isinstance(messages, str):
        return re.sub(date_pattern, convert_date, messages)
    else:
        return messages
