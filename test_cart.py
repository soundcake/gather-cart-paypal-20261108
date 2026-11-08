import copy
import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import HTTPServer
from http.cookiejar import CookieJar
from cart import (validate_request, parse_selection, make_plan, QuoteStore,
                  order_payload, verify_order, capture_receipt)
from paypal import Sandbox, create_checkout, finish_checkout
from providers import Samples, SAMPLES
from server import make_handler


def request(**changes):
    body = dict(brief=SAMPLES['sketching'], people=12, budget='30.00', owned=[])
    body.update(changes)
    return body


def quote(store=None, owner='owner'):
    store = store or QuoteStore()
    plan = make_plan(validate_request(request()), parse_selection('{"activity":"sketching","reason":"Pencil drawing."}'))
    return store.get(store.put(plan, owner), owner)


class FakePayPal:
    ready = True

    def __init__(self):
        self.calls = []
        self.data = None

    def request(self, method, path, body=None, request_id=None):
        self.calls.append((method, path, copy.deepcopy(body), request_id))
        if path.endswith('/capture'):
            self.data['status'] = 'COMPLETED'
            self.data['purchase_units'][0]['payments'] = {'captures': [dict(
                id='CAPTURE12345', status='COMPLETED', amount={'currency_code': 'USD', 'value': '20.70'})]}
        elif method == 'POST':
            self.data = dict(id='ORDER123456', status='CREATED', purchase_units=copy.deepcopy(body['purchase_units']),
                             links=[{'rel': 'payer-action', 'href': 'https://www.sandbox.paypal.com/checkoutnow?token=ORDER123456'}])
        return copy.deepcopy(self.data)


class CartTests(unittest.TestCase):
    def test_rounds_up_whole_packs(self):
        p = make_plan(validate_request(request(people=13)), dict(activity='sketching', reason='Drawing'))
        self.assertEqual([r['quantity'] for r in p['rows']], [1, 3, 4])
        self.assertEqual(p['total_cents'], 2740)

    def test_owned_materials_remove_cost(self):
        p = make_plan(validate_request(request(owned=['paper', 'pencils', 'erasers'])), dict(activity='sketching', reason='Drawing'))
        self.assertEqual(p['total_cents'], 0)
        self.assertFalse(p['checkout_allowed'])

    def test_over_budget_blocks_checkout(self):
        p = make_plan(validate_request(request(budget='1.00')), dict(activity='sketching', reason='Drawing'))
        s = QuoteStore(); qid = s.put(p, 'me')
        with self.assertRaisesRegex(ValueError, 'cannot be checked out'):
            s.get(qid, 'me', for_payment=True)

    def test_money_inputs_reject_ambiguous_and_nonfinite(self):
        for value in ['NaN', '1e2', '2.001', '-5', '1,000', '0', '1001', 20, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_request(request(budget=value))

    def test_unknown_duplicate_and_malformed_owned_items(self):
        for owned in [['paper', 'paper'], ['unknown'], [{}], [None], 'paper']:
            with self.subTest(owned=owned), self.assertRaises(ValueError):
                validate_request(request(owned=owned))

    def test_model_cannot_add_prices_or_unknown_activity(self):
        for data in [{'activity': 'sketching', 'reason': 'a', 'price': 1},
                     {'activity': 'unsupported', 'reason': 'a'}, {'activity': [], 'reason': 'a'}]:
            with self.assertRaises(ValueError):
                parse_selection(json.dumps(data))

    def test_quotes_are_isolated_immutable_and_expire(self):
        now = [0]; s = QuoteStore(clock=lambda: now[0]); q = quote(s)
        with self.assertRaises(ValueError):
            s.get(q['id'], 'different')
        p = copy.deepcopy(q['plan']); q2 = s.put(p, 'owner'); p['total_cents'] = 1
        self.assertEqual(s.get(q2, 'owner')['plan']['total_cents'], 2070)
        now[0] = 1801
        with self.assertRaisesRegex(ValueError, 'expired'):
            s.get(q2, 'owner', for_payment=True)

    def test_order_amount_equals_exact_item_sum(self):
        q = quote(); payload = order_payload(q, 'http://127.0.0.1:8786')
        unit = payload['purchase_units'][0]
        self.assertEqual(unit['amount']['value'], '20.70')
        self.assertEqual(unit['custom_id'], q['id'])
        self.assertEqual([i['quantity'] for i in unit['items']], ['1', '2', '3'])

    def test_checkout_retries_keep_order_and_request_key(self):
        q = quote(); g = FakePayPal()
        first = create_checkout(g, q, 'http://127.0.0.1:8786')
        self.assertEqual(create_checkout(g, q, 'http://127.0.0.1:8786'), first)
        self.assertEqual(len(g.calls), 1)
        self.assertEqual(g.calls[0][3], q['create_key'])

    def test_capture_requires_approval_then_returns_same_receipt(self):
        q = quote(); g = FakePayPal(); create_checkout(g, q, 'http://127.0.0.1:8786')
        with self.assertRaisesRegex(ValueError, 'Approve'):
            finish_checkout(g, q)
        g.data['status'] = 'APPROVED'
        receipt = finish_checkout(g, q)
        self.assertEqual(receipt['amount']['value'], '20.70')
        calls = len(g.calls)
        self.assertEqual(finish_checkout(g, q), receipt)
        self.assertEqual(len(g.calls), calls)

    def test_changed_gateway_amount_prevents_capture(self):
        q = quote(); g = FakePayPal(); create_checkout(g, q, 'http://127.0.0.1:8786')
        g.data['status'] = 'APPROVED'; g.data['purchase_units'][0]['amount']['value'] = '0.01'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            finish_checkout(g, q)
        self.assertFalse(any(call[1].endswith('/capture') for call in g.calls))

    def test_wrong_capture_amount_and_pending_status_rejected(self):
        q = quote(); g = FakePayPal(); create_checkout(g, q, 'http://127.0.0.1:8786')
        g.data['status'] = 'APPROVED'; finish_checkout(g, q)
        bad = copy.deepcopy(g.data); bad['purchase_units'][0]['payments']['captures'][0]['status'] = 'PENDING'
        with self.assertRaises(ValueError):
            capture_receipt(bad, q)
        bad['purchase_units'][0]['payments']['captures'][0]['status'] = 'COMPLETED'
        bad['purchase_units'][0]['payments']['captures'][0]['amount']['value'] = '0.01'
        with self.assertRaises(ValueError):
            capture_receipt(bad, q)

    def test_external_approval_link_is_rejected(self):
        q = quote(); g = FakePayPal()
        original = g.request
        def request_bad(*args):
            data = original(*args); data['links'][0]['href'] = 'https://www.paypal.com/checkoutnow'
            return data
        g.request = request_bad
        with self.assertRaisesRegex(ValueError, 'non-sandbox'):
            create_checkout(g, q, 'http://127.0.0.1:8786')
        self.assertIsNone(q['paypal_id'])

    def test_sample_mode_does_not_guess_arbitrary_brief(self):
        with self.assertRaises(ValueError):
            Samples().choose('Buy a computer for everyone')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), make_handler(Samples(), FakePayPal(), QuoteStore(), 'placeholder', 'samples'))
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.server.RequestHandlerClass = make_handler(Samples(), FakePayPal(), QuoteStore(), self.origin, 'samples')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(CookieJar()))
        with self.opener.open(self.origin + '/api/config') as response:
            self.cookie_header = response.headers['Set-Cookie']
            self.config = json.load(response)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def post(self, path, body, origin=None, csrf=None):
        req = urllib.request.Request(self.origin + path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Origin': origin or self.origin,
                     'X-CSRF-Token': csrf or self.config['csrf']})
        with self.opener.open(req) as response:
            return json.load(response)

    def test_http_plan_and_quote_restore(self):
        r = self.post('/api/plan', request())
        self.assertEqual(r['plan']['total_cents'], 2070)
        with self.opener.open(self.origin + '/api/quote?id=' + r['id']) as response:
            self.assertEqual(json.load(response)['plan'], r['plan'])

    def test_session_cookie_allows_top_level_paypal_return(self):
        self.assertIn('SameSite=Lax', self.cookie_header)
        self.assertIn('HttpOnly', self.cookie_header)

    def test_samples_cannot_create_orders(self):
        q = self.post('/api/plan', request())
        self.assertFalse(self.config['checkout_ready'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post('/api/checkout', {'quote_id': q['id']})
        self.assertEqual(caught.exception.code, 400)

    def test_cross_origin_and_wrong_csrf_rejected(self):
        for headers in [{'origin': 'https://other.example'}, {'csrf': 'invalid'}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post('/api/plan', request(), **headers)
            self.assertEqual(caught.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
