"""PayPal sandbox only; no production endpoint or automatic payment approval."""
import base64
import json
import os
import re
import time
import urllib.request
from urllib.parse import urlsplit
from cart import order_payload, verify_order, capture_receipt
BASE='https://api-m.sandbox.paypal.com'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class Sandbox:
    def __init__(self):
        self.ready=os.environ.get('ENABLE_PAYPAL_SANDBOX')=='1' and bool(os.environ.get('PAYPAL_SANDBOX_CLIENT_ID')) and bool(os.environ.get('PAYPAL_SANDBOX_CLIENT_SECRET'))
        self.token=None;self.expires=0
        self.opener=urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None, request_id=None):
        if not self.ready:raise ValueError('PayPal sandbox access is not configured. No order has been sent.')
        if not re.fullmatch(r'/v2/checkout/orders(?:/[A-Z0-9]+(?:/capture)?)?',path):raise ValueError('Unsupported PayPal operation.')
        if time.time()>=self.expires:
            credentials=(os.environ['PAYPAL_SANDBOX_CLIENT_ID']+':'+os.environ['PAYPAL_SANDBOX_CLIENT_SECRET']).encode()
            req=urllib.request.Request(BASE+'/v1/oauth2/token',data=b'grant_type=client_credentials',headers={'Authorization':'Basic '+base64.b64encode(credentials).decode(),'Content-Type':'application/x-www-form-urlencoded'},method='POST')
            with self.opener.open(req,timeout=20) as r:token=json.load(r)
            self.token=token['access_token'];self.expires=time.time()+max(0,int(token['expires_in'])-60)
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Prefer':'return=representation'}
        if request_id:headers['PayPal-Request-Id']=request_id
        req=urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        with self.opener.open(req,timeout=20) as r:return json.load(r)


def create_checkout(gateway, quote, origin):
    if quote['receipt']:return {'receipt':quote['receipt']}
    if quote['paypal_id']:return {'approval_url':quote['approval_url'],'order_id':quote['paypal_id']}
    data=gateway.request('POST','/v2/checkout/orders',order_payload(quote,origin),quote['create_key'])
    pid=data.get('id','')
    links=[x.get('href','') for x in data.get('links',[]) if x.get('rel') in {'approve','payer-action'}]
    if not re.fullmatch(r'[A-Z0-9]{8,40}',pid) or len(links)!=1:raise ValueError('PayPal did not return a valid approval link. Retry the same basket.')
    link=urlsplit(links[0])
    if link.scheme!='https' or link.hostname!='www.sandbox.paypal.com' or link.port not in (None,443) or link.username or link.password:
        raise ValueError('PayPal returned a non-sandbox approval destination.')
    quote.update(paypal_id=pid,approval_url=links[0])
    return {'approval_url':links[0],'order_id':pid}


def finish_checkout(gateway, quote):
    if quote['receipt']:return quote['receipt']
    if not quote['paypal_id']:raise ValueError('Create and approve the PayPal sandbox order first.')
    data=gateway.request('GET','/v2/checkout/orders/'+quote['paypal_id'])
    verify_order(data,quote)
    if data['status']!='COMPLETED':data=gateway.request('POST','/v2/checkout/orders/'+quote['paypal_id']+'/capture',{},quote['capture_key'])
    receipt=capture_receipt(data,quote);quote['receipt']=receipt;return receipt
