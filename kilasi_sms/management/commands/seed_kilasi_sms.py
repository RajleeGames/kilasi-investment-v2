from django.core.management.base import BaseCommand
from sms_center.models import SmsTemplate

PRESETS = [
 ('Retail','Thank you after sale','Ndugu {first_name}, asante kwa kununua Kilasi Investment. Risiti yako: {reference}. Karibu tena.'),
 ('Retail','Sale follow-up','Ndugu {first_name}, tunakushukuru kwa kuchagua Kilasi Investment. Kwa msaada kuhusu manunuzi {reference}, wasiliana nasi.'),
 ('Retail','Proforma sent','Ndugu {first_name}, proforma yako {reference} imeandaliwa na Kilasi Investment. Wasiliana nasi kwa maelezo zaidi.'),
 ('Retail','Delivery prepared','Ndugu {first_name}, maandalizi ya delivery ya bidhaa zako {reference} yanaendelea. Tutakujulisha hatua inayofuata.'),
 ('Retail','Delivery confirmation','Ndugu {first_name}, asante kwa kupokea bidhaa zako kutoka Kilasi Investment. Rejea: {reference}.'),
 ('Retail','Orders — future module','Ndugu {first_name}, oda yako {reference} imepokelewa na Kilasi Investment. Tutakujulisha hatua inayofuata.'),
 ('Retail','Genji / top-up — future module','Ndugu {first_name}, tunakushukuru kwa huduma ya kubadilisha bidhaa Kilasi Investment. Rejea: {reference}.'),
 ('Transport','Booking received','Ndugu {first_name}, booking yako {reference} imepokelewa HAMA NA KILASI: {pickup} hadi {destination}. Tutakujulisha ikithibitishwa.'),
 ('Transport','Booking confirmed','Ndugu {first_name}, booking yako {reference} imethibitishwa. Kutoka {pickup} kwenda {destination}. Ahsante.'),
 ('Transport','Before transport','Ndugu {first_name}, kumbusho la safari yako {reference} kutoka {pickup} kwenda {destination} tarehe {date}. Tafadhali kuwa tayari.'),
 ('Transport','Transport underway','Ndugu {first_name}, safari yako {reference} kutoka {pickup} kwenda {destination} inaendelea. Asante kwa HAMA NA KILASI.'),
 ('Transport','Transport completed','Ndugu {first_name}, asante kwa kutumia HAMA NA KILASI. Safari {reference} imekamilika. Karibu tena.'),
 ('Transport','Transport follow-up','Ndugu {first_name}, tunakushukuru kwa huduma ya usafiri {reference}. Kwa maswali au maoni wasiliana na Kilasi Investment.'),
]

class Command(BaseCommand):
    help = 'Create idempotent Kilasi service-notification SMS presets. Never sends SMS.'
    def handle(self, *args, **kwargs):
        n=0
        for department,name,body in PRESETS:
            _,created=SmsTemplate.objects.get_or_create(name=f'KI {department}: {name}', defaults={
                'category':f'Kilasi {department}', 'body':body,'is_active':True})
            n+=int(created)
        self.stdout.write(self.style.SUCCESS(f'Added {n} templates; kept existing edited templates unchanged.'))
