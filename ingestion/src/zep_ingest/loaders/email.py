"""EmlLoader: RFC-822 .eml files → message episodes.

Parses standard email exports (e.g. from an email client's "save as .eml",
Google Takeout, or an mbox split into messages) with the stdlib email package.
The Date header becomes the episode's created_at so backfilled correspondence
carries its real timeline; a missing Date surfaces through the pipeline's
missing-timestamp warning.
"""

import glob
import html
import re
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode

_HTML_DROP = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_BREAK = re.compile(r"</?(?:p|div|br|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def _html_to_text(markup: str) -> str:
    text = _HTML_DROP.sub("", markup)
    text = _HTML_BREAK.sub("\n", text)
    text = _HTML_TAG.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class EmlLoader:
    def __init__(self, path_or_glob: str | Path) -> None:
        self.pattern = str(path_or_glob)
        self.files = sorted(
            Path(p) for p in glob.glob(self.pattern, recursive=True) if Path(p).is_file()
        )
        if not self.files:
            raise ConfigurationError(f"No .eml files match {self.pattern!r}.")

    def load(self) -> Iterator[Episode]:
        for file in self.files:
            message = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
            body_part = message.get_body(preferencelist=("plain",))
            if body_part is not None:
                body = str(body_part.get_content()).strip()
            else:
                # HTML-only mail (common for marketing and some clients):
                # tag-stripped html beats silently ingesting an empty body
                html_part = message.get_body(preferencelist=("html",))
                body = _html_to_text(str(html_part.get_content())) if html_part else ""
            sender = str(message.get("From", "unknown sender"))
            recipient = str(message.get("To", "unknown recipient"))
            subject = str(message.get("Subject", "(no subject)"))
            date_header = message.get("Date")
            created_at = None
            if date_header:
                try:
                    created_at = parsedate_to_datetime(str(date_header)).isoformat()
                except ValueError:
                    created_at = None
            yield Episode(
                data=f"Email from {sender} to {recipient} (subject: {subject}):\n{body}",
                data_type="message",
                created_at=created_at,
                metadata={"source": "email", "subject": subject[:100]},
                source_description=f"email export ({file.name})",
            )
