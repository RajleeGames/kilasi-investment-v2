"""
KILASI INVESTMENT V2
Production operation safeguards.

- Allow SMS operations only when explicitly enabled.
- Preserve historical imported sales/payment immutability.
- Do not modify SQLite production protection triggers.
"""

import re
import sqlite3

from pathlib import Path
from contextlib import closing

from django.conf import settings
from django.http import HttpResponseForbidden


SALE_VOID = re.compile(
    r"^/sales/(\d+)/void/?$"
)

PAYMENT_VOID = re.compile(
    r"^/debts/payments/(\d+)/void/?$"
)


class GuardedOperations:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        path = request.path.lower()

        if request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):

            # ==========================================
            # SMS OPERATIONS
            # ==========================================

            sms_paths = (
                "/sms/",
                "/retail/sms/",
                "/transport/sms/",
                "/api/sms/",
            )

            if path.startswith(sms_paths):

                sms_enabled = bool(
                    getattr(
                        settings,
                        "KILASI_SMS_LIVE_ENABLED",
                        False,
                    )
                )

                if not sms_enabled:
                    return HttpResponseForbidden(
                        "SMS operations are disabled "
                        "in production configuration."
                    )

            # ==========================================
            # HISTORICAL SALES / PAYMENT PROTECTION
            # ==========================================

            match = SALE_VOID.fullmatch(path)
            source_type = "sales_invoice"

            if not match:
                match = PAYMENT_VOID.fullmatch(path)
                source_type = "sales_payment"

            if match:

                db_path = Path(
                    settings.DATABASES["default"]["NAME"]
                ).resolve()

                db_uri = (
                    db_path.as_uri() + "?mode=ro"
                )

                with closing(
                    sqlite3.connect(
                        db_uri,
                        uri=True,
                    )
                ) as connection:

                    historical = connection.execute(
                        """
                        SELECT 1
                        FROM kilasi_import_source_map
                        WHERE source_table = ?
                          AND target_id = ?
                        LIMIT 1
                        """,
                        (
                            source_type,
                            int(match.group(1)),
                        ),
                    ).fetchone()

                    if historical:
                        return HttpResponseForbidden(
                            "This imported historical "
                            "transaction is immutable."
                        )

        return self.get_response(request)
