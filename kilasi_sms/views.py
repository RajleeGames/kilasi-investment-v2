from types import SimpleNamespace
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from sms_center.models import SmsTemplate, SmsSenderProfile
from .audiences import SOURCE_LABELS, STATUS_LABELS, audience_rows, parse_filters
from .models import KilasiSmsDispatch
from .permissions import scope
from .services import draft, confirm_send, live_ready, blocked_numbers, MAX_SMS_PER_DRAFT


def _urls(request):
    ns=request.resolver_match.namespace
    return {'hub_url':reverse(f'{ns}:hub'), 'draft_url':reverse(f'{ns}:draft'),
            'history_url':reverse(f'{ns}:history'), 'namespace':ns}


def _templates(department):
    return SmsTemplate.objects.filter(is_active=True, category=f'Kilasi {department.title()}').order_by('name')


def _sender_profiles():
    return SmsSenderProfile.objects.filter(is_active=True).order_by('-is_default','sender_id')


@require_http_methods(['GET'])
def hub(request):
    department,branch=scope(request)
    try:
        f=parse_filters(request.GET,department)
        rows=audience_rows(department,branch,f)
    except ValidationError as exc:
        messages.error(request,exc.messages[0]);return redirect(_urls(request)['hub_url'])
    blocked=blocked_numbers([r['phone'] for r in rows])
    for r in rows:r['blocked'] = r['phone'] in blocked
    templates=list(_templates(department))
    chosen=request.GET.get('template','')
    picked=next((t for t in templates if str(t.pk)==chosen),None)
    drafts=(KilasiSmsDispatch.objects.filter(**({'department':department,'branch':branch} if department=='retail' else {'department':department}))
            .select_related('campaign','created_by').order_by('-created_at')[:5])
    return render(request,'kilasi_sms/hub.html',{
        **_urls(request), 'department':department, 'branch':(branch if department=='retail' else SimpleNamespace(name='HAMA NA KILASI')),'filters':f,
        'rows':Paginator(rows,50).get_page(request.GET.get('page')),
        'total_rows':len(rows),'sources':SOURCE_LABELS[department],
        'status_options':STATUS_LABELS[department],
        'templates':templates,'picked':picked,'sender_profiles':_sender_profiles(),
        'drafts':drafts, 'limit':MAX_SMS_PER_DRAFT,'live_ready':live_ready(),
    })


@require_POST
def create_draft(request):
    department,branch=scope(request)
    urls=_urls(request)
    try:
        f=parse_filters(request.POST,department)
        rows=audience_rows(department,branch,f)
        allowed={r['key']:r for r in rows}
        tokens=request.POST.getlist('selected')
        if len(tokens)>MAX_SMS_PER_DRAFT:
            raise ValidationError(f'Select at most {MAX_SMS_PER_DRAFT} rows.')
        chosen=[allowed[k] for k in dict.fromkeys(tokens) if k in allowed]
        if len(chosen)!=len(set(tokens)) or any(k not in allowed for k in tokens):
            raise ValidationError('Selection is invalid or outside the chosen department and filters.')
        if request.POST.get('confirm_relationship') != 'on':
            raise ValidationError('Confirm each recipient has a relevant transaction/service relationship.')
        sender=request.POST.get('sender_id','').strip().upper()
        template_id=request.POST.get('template_id','').strip()
        template=None
        if template_id.isdigit():
            template=_templates(department).filter(pk=int(template_id)).first()
            if not template:raise ValidationError('Template is not available to this department.')
        body=(request.POST.get('message') or '').strip() or (template.body if template else '')
        record=draft(department=department,branch=branch,creator=request.user,
            source=f['source'],title=request.POST.get('title','').strip(),
            sender_id=sender,body=body,recipients=chosen)
    except (ValidationError,IntegrityError) as exc:
        error=exc.messages[0] if isinstance(exc,ValidationError) else 'Could not save the draft.'
        messages.error(request,error)
        return redirect(urls['hub_url'])
    messages.success(request,'Draft saved. Review every message before live sending.')
    return redirect(reverse(f'{urls["namespace"]}:detail',kwargs={'pk':record.pk}))


def _record(request, pk, department, branch):
    return get_object_or_404(KilasiSmsDispatch.objects.select_related('campaign','created_by'),
                             **({'pk':pk,'department':department,'branch':branch} if department=='retail' else {'pk':pk,'department':department}))


@require_http_methods(['GET'])
def detail(request,pk):
    department,branch=scope(request)
    obj=_record(request,pk,department,branch)
    recipients=obj.campaign.recipients.order_by('pk')
    return render(request,'kilasi_sms/detail.html',{
        **_urls(request), 'department':department,'branch':(branch if department=='retail' else SimpleNamespace(name='HAMA NA KILASI')),'record':obj,
        'recipients':recipients,'live_ready':live_ready(),
        'confirm_url':reverse(f'{request.resolver_match.namespace}:confirm',kwargs={'pk':pk}),
    })


@require_POST
def confirm(request,pk):
    department,branch=scope(request)
    obj=_record(request,pk,department,branch)
    url=reverse(f'{request.resolver_match.namespace}:detail',kwargs={'pk':pk})
    if request.POST.get('confirm_send') != 'yes':
        messages.error(request,'Explicit confirmation is required.');return redirect(url)
    try:
        obj,accepted,failed=confirm_send(dispatch_id=obj.pk,department=department,branch=branch)
    except ValidationError as exc:
        messages.error(request,exc.messages[0]);return redirect(url)
    messages.info(request,f'Beem accepted {accepted}; failed or blocked {failed}. Acceptance is not delivery.')
    return redirect(url)


@require_http_methods(['GET'])
def history(request):
    department,branch=scope(request)
    qs=KilasiSmsDispatch.objects.filter(**({'department':department,'branch':branch} if department=='retail' else {'department':department})).select_related('campaign','created_by')
    page=Paginator(qs,30).get_page(request.GET.get('page'))
    for r in page.object_list:
        r.detail_url=reverse(f'{request.resolver_match.namespace}:detail',kwargs={'pk':r.pk})
    return render(request,'kilasi_sms/history.html',{
        **_urls(request), 'department':department,'branch':(branch if department=='retail' else SimpleNamespace(name='HAMA NA KILASI')),'records':page,
    })
