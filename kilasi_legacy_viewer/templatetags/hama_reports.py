
"""
KILASI INVESTMENT V2
HAMA NA KILASI - Historical dashboard integration.

Works with:
- A fresh local development database.
- An activated production database containing verified history.

Never creates, changes, imports, or deletes historical records.
"""

from contextlib import closing

from django import template
from django.conf import settings

from kilasi_legacy_viewer.hama_final_views import (
    _filters,
    historical_summary,
)
from kilasi_legacy_viewer.views import (
    archival_conn,
    can_view_history,
)


register = template.Library()


def historical_archive_available():
    """
    Check whether the historical closeout archive exists.

    Uses a read-only database connection.
    Does not manufacture legacy tables or historic results.
    """

    with closing(archival_conn()) as connection:

        result = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'kilasi_stage7h_manifest'
            LIMIT 1
            """
        ).fetchone()

        return result is not None


@register.inclusion_tag(
    "kilasi_legacy_viewer/hama_dashboard_panel.html",
    takes_context=True,
)
def hama_dashboard_panel(context):

    request = context["request"]

    # Historical financial information is superuser-only.
    if not can_view_history(request.user):
     return {"hama_hidden": True}

    # Fresh development databases have no migration archive.
    if not historical_archive_available():

        # Production must never silently lose its
        # verified historical reporting.
        if (
            settings.SETTINGS_MODULE
            == "config.settings_kilasi_production"
        ):
            raise RuntimeError(
                "Production HAMA historical archive is missing."
            )

        return {"hama_hidden": True}

    # Archive exists: preserve the original closeout
    # validation and historical financial calculations.
    start, end = _filters(request)

    return {
        "hama": historical_summary(None, start, end),
        "request": request,
        "hama_hidden": False,
    }
