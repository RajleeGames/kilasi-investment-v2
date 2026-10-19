"""Enabled in isolated Stage7C settings only. Production never gets these links."""
def staging_nav(request):
    p = request.path
    sections = ('/legacy/', '/transport/', '/trade/', '/sms/history/', '/transport/sms/history/')
    return {'kilasi_stage_native_ui': True,
            'kilasi_stage_nav_open': any(p.startswith(t) for t in sections)}
