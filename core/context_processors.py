from .models import ShopSettings
def shop_context(request):
    return {'shop': ShopSettings.get_solo()}


from .novman_branch import branch_context
_original_shop_context = shop_context

def shop_context(request):
    data = _original_shop_context(request)
    data.update(branch_context(request))
    return data
