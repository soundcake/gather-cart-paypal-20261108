"""Deterministic workshop quantities, prices and immutable checkout quotes."""
import copy
import json
import math
import re
import time
import uuid
from decimal import Decimal

CATALOG = [
    dict(id='paper', name='Drawing paper · 20 sheets', cents=450, serves=20, activity='sketching'),
    dict(id='pencils', name='Graphite pencils · 6 pack', cents=390, serves=6, activity='sketching'),
    dict(id='erasers', name='Soft erasers · 4 pack', cents=280, serves=4, activity='sketching'),
    dict(id='pots', name='Starter pots · 12 pack', cents=600, serves=12, activity='seed-starting'),
    dict(id='seeds', name='Basil seed portions · 12 pack', cents=480, serves=12, activity='seed-starting'),
    dict(id='soil', name='Growing medium · 12 portions', cents=720, serves=12, activity='seed-starting'),
    dict(id='pages', name='Folded notebook pages · 6 sets', cents=660, serves=6, activity='bookbinding'),
    dict(id='covers', name='Card covers · 6 pairs', cents=420, serves=6, activity='bookbinding'),
    dict(id='cord', name='Binding cord · 12 lengths', cents=360, serves=12, activity='bookbinding'),
]
BY_ID = {x['id']: x for x in CATALOG}
ACTIVITIES = {'sketching': 'Sketch together', 'seed-starting': 'Start a seed garden', 'bookbinding': 'Make simple notebooks'}


def money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def validate_request(body):
    if not isinstance(body, dict) or set(body) != {'brief', 'people', 'budget', 'owned'}:
        raise ValueError('Provide the brief, group size, budget and materials already available.')
    brief = body['brief']
    if not isinstance(brief, str) or not 8 <= len(brief.strip()) <= 600:
        raise ValueError('Describe your workshop in 8–600 characters.')
    people = body['people']
    if type(people) is not int or not 1 <= people <= 60:
        raise ValueError('Choose a group size from 1 to 60.')
    budget = body['budget']
    if not isinstance(budget, str) or not re.fullmatch(r'(?:0|[1-9]\d{0,3})(?:\.\d{1,2})?', budget):
        raise ValueError('Enter the budget in dollars, using at most two decimal places.')
    cents = int(Decimal(budget) * 100)
    if not 100 <= cents <= 100000:
        raise ValueError('Use a budget between $1 and $1,000.')
    owned = body['owned']
    if not isinstance(owned, list) or any(not isinstance(x, str) for x in owned) or len(owned) != len(set(owned)) or any(x not in BY_ID for x in owned):
        raise ValueError('Select materials from the catalogue.')
    return dict(brief=brief.strip(), people=people, budget_cents=cents, owned=owned)


def parse_selection(text):
    """The model selects an activity; it cannot supply prices or order quantities."""
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError('The model did not return a usable activity. Try a clearer workshop brief.') from exc
    if not isinstance(obj, dict) or set(obj) != {'activity', 'reason'}:
        raise ValueError('The model returned an unexpected selection format.')
    if not isinstance(obj['activity'], str) or obj['activity'] not in ACTIVITIES:
        raise ValueError('This brief is outside the three supported workshops. Try drawing, seed planting or notebook making.')
    if not isinstance(obj['reason'], str) or not 1 <= len(obj['reason']) <= 350:
        raise ValueError('The model did not give a concise explanation.')
    return obj


def make_plan(request, selection):
    rows = []
    for item in CATALOG:
        if item['activity'] != selection['activity']:
            continue
        have = item['id'] in request['owned']
        quantity = 0 if have else math.ceil(request['people'] / item['serves'])
        rows.append(dict(item, quantity=quantity, total_cents=quantity * item['cents'], already_have=have))
    total = sum(row['total_cents'] for row in rows)
    return dict(activity=selection['activity'], title=ACTIVITIES[selection['activity']], reason=selection['reason'], brief=request['brief'],
                people=request['people'], budget_cents=request['budget_cents'], rows=rows, total_cents=total,
                remaining_cents=request['budget_cents']-total, checkout_allowed=0 < total <= request['budget_cents'], currency='USD')


class QuoteStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.quotes = {}

    def put(self, plan, owner):
        for key in list(self.quotes):
            if self.clock() - self.quotes[key]['created_at'] > 7200:
                del self.quotes[key]
        if len(self.quotes) >= 200:
            raise ValueError('The local quote limit was reached. Restart the demo to clear old quotes.')
        qid = str(uuid.uuid4())
        self.quotes[qid] = dict(id=qid, plan=copy.deepcopy(plan), owner=owner, created_at=self.clock(),
                               create_key=str(uuid.uuid4()), capture_key=str(uuid.uuid4()), paypal_id=None, approval_url=None, receipt=None)
        return qid

    def get(self, qid, owner, for_payment=False):
        q = self.quotes.get(qid)
        if not q or q['owner'] != owner:
            raise ValueError('This basket is unavailable in this browser session.')
        if for_payment and self.clock()-q['created_at'] > 1800:
            raise ValueError('This quote expired. Plan the workshop again before checkout.')
        if for_payment and not q['plan']['checkout_allowed']:
            raise ValueError('This basket cannot be checked out. Review its budget or materials.')
        return q


def order_payload(quote, origin):
    p = quote['plan']; value = dict(currency_code='USD', value=money(p['total_cents']))
    return dict(intent='CAPTURE', purchase_units=[dict(reference_id=quote['id'], custom_id=quote['id'],
        amount=dict(value, breakdown={'item_total': value}),
        items=[dict(name=x['name'], sku=x['id'], quantity=str(x['quantity']), unit_amount=dict(currency_code='USD',value=money(x['cents'])), category='PHYSICAL_GOODS') for x in p['rows'] if x['quantity']])],
        payment_source={'paypal': {'experience_context': dict(brand_name='Gather Cart Sandbox', user_action='PAY_NOW', shipping_preference='NO_SHIPPING', return_url=origin+'/?quote='+quote['id'], cancel_url=origin+'/?cancel='+quote['id'])}})


def verify_order(data, quote):
    units = data.get('purchase_units', [])
    if data.get('id') != quote['paypal_id'] or len(units) != 1:
        raise ValueError('PayPal returned a different order.')
    unit = units[0]
    expected = {'currency_code': 'USD', 'value': money(quote['plan']['total_cents'])}
    if unit.get('custom_id') != quote['id'] or unit.get('amount') != expected and {k:unit.get('amount',{}).get(k) for k in expected} != expected:
        raise ValueError('PayPal order amount or basket reference does not match the reviewed quote.')
    if data.get('status') not in {'APPROVED', 'COMPLETED'}:
        raise ValueError('Approve this order in the PayPal sandbox first.')


def capture_receipt(data, quote):
    if data.get('id') != quote['paypal_id'] or data.get('status') != 'COMPLETED':
        raise ValueError('PayPal has not confirmed completion. Do not treat this order as paid.')
    units = data.get('purchase_units', [])
    if len(units) != 1:
        raise ValueError('PayPal returned an unexpected receipt.')
    captures = units[0].get('payments', {}).get('captures', [])
    expected = {'currency_code':'USD','value':money(quote['plan']['total_cents'])}
    if len(captures) != 1 or captures[0].get('status') != 'COMPLETED' or captures[0].get('amount') != expected or not captures[0].get('id'):
        raise ValueError('PayPal has not confirmed the exact quoted amount.')
    return dict(status='COMPLETED', order_id=quote['paypal_id'], capture_id=captures[0]['id'], amount=expected, sandbox=True)
