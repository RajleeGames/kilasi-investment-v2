"""No changes to historical imported sales/payments or SMS sending in first release.
SQLite triggers independently enforce historical immutability even outside HTTP.
"""
import re,sqlite3
from pathlib import Path
from contextlib import closing
from django.conf import settings
from django.http import HttpResponseForbidden

_SALE_VOID=re.compile(r'^/sales/(\d+)/void/?$')
_PAYMENT_VOID=re.compile(r'^/debts/payments/(\d+)/void/?$')
class GuardedOperations:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        path=request.path.lower()
        if request.method not in ('GET','HEAD','OPTIONS'):
            if path.startswith(('/sms/','/transport/sms/','/api/sms/')):
                return HttpResponseForbidden('Sending and modifying SMS is disabled at Kilasi production launch.')
            m=_SALE_VOID.fullmatch(path);typ='sales_invoice'
            if not m:m=_PAYMENT_VOID.fullmatch(path);typ='sales_payment'
            if m:
                source=settings.DATABASES['default']['NAME']
                with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as c:
                    if c.execute('SELECT 1 FROM kilasi_import_source_map WHERE source_table=? AND target_id=? LIMIT 1',(typ,int(m.group(1)))).fetchone():
                        return HttpResponseForbidden('This imported historical transaction is immutable.')
        return self.get_response(request)
