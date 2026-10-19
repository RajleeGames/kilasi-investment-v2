"""Local staging circuit-breaker: business-changing HTTP requests forbidden."""
from django.http import HttpResponseForbidden

class BlockStageMutations:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Logging in/out is required to view private archival records.
        # Stage7C is loopback-only and has SMS provider keys removed.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            allowed = {'/login/', '/logout/'}
            if request.path not in allowed:
                return HttpResponseForbidden('Stage7C: read-only archive; changes are disabled.')
        return self.get_response(request)
