"""Tests for EmlLoader — RFC-822 .eml files → message episodes."""

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.loaders.email import EmlLoader

EML = """\
From: Avery Brown <avery@example.com>
To: Team <team@example.com>
Date: Tue, 14 Apr 2026 09:15:00 -0400
Subject: Kickoff priorities
Content-Type: text/plain; charset="utf-8"

Three priorities this quarter. Blake Carter owns delivery.
"""

EML_NO_DATE = """\
From: someone@example.com
To: other@example.com
Subject: undated
Content-Type: text/plain; charset="utf-8"

No date header here.
"""


@pytest.fixture
def eml_dir(tmp_path):
    (tmp_path / "01_kickoff.eml").write_text(EML)
    (tmp_path / "02_undated.eml").write_text(EML_NO_DATE)
    return tmp_path


def test_one_message_episode_per_file(eml_dir):
    episodes = list(EmlLoader(str(eml_dir / "*.eml")).load())
    assert len(episodes) == 2
    assert all(e.data_type == "message" for e in episodes)


def test_headers_and_body_in_data(eml_dir):
    [episode] = EmlLoader(str(eml_dir / "01_kickoff.eml")).load()
    assert "Avery Brown <avery@example.com>" in episode.data
    assert "Kickoff priorities" in episode.data
    assert "Blake Carter owns delivery." in episode.data


def test_created_at_from_date_header(eml_dir):
    [episode] = EmlLoader(str(eml_dir / "01_kickoff.eml")).load()
    assert episode.created_at == "2026-04-14T09:15:00-04:00"


def test_missing_date_header_leaves_created_at_none(eml_dir):
    [episode] = EmlLoader(str(eml_dir / "02_undated.eml")).load()
    assert episode.created_at is None  # pipeline will warn about it


def test_metadata_and_source_description(eml_dir):
    [episode] = EmlLoader(str(eml_dir / "01_kickoff.eml")).load()
    assert episode.metadata["source"] == "email"
    assert episode.metadata["subject"] == "Kickoff priorities"
    assert episode.source_description == "email export (01_kickoff.eml)"


EML_HTML_ONLY = """\
From: promo@example.com
To: avery@example.com
Date: Wed, 15 Apr 2026 08:00:00 -0400
Subject: Launch update
Content-Type: text/html; charset="utf-8"

<html><head><style>p { color: red; }</style></head>
<body><p>ROBOT-202 ships in <b>May</b>.</p><p>Reply for details.</p></body></html>
"""


def test_html_only_email_falls_back_to_stripped_html(tmp_path):
    (tmp_path / "promo.eml").write_text(EML_HTML_ONLY)
    [episode] = EmlLoader(str(tmp_path / "promo.eml")).load()
    assert "ROBOT-202 ships in May." in episode.data
    assert "Reply for details." in episode.data
    assert "<p>" not in episode.data
    assert "color: red" not in episode.data


def test_no_match_raises_eagerly(tmp_path):
    with pytest.raises(ConfigurationError):
        EmlLoader(str(tmp_path / "*.eml"))


def test_ingest_emails_one_liner(mock_zep, eml_dir):
    from zep_ingest.pipeline import ingest_emails

    result = ingest_emails(mock_zep, str(eml_dir / "*.eml"), graph_id="mail")
    assert result.items_submitted == 2
    items = mock_zep.batch.add.call_args.kwargs["items"]
    assert all(i.data_type == "message" for i in items)
