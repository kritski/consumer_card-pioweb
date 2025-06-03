from flask import Flask, request, jsonify, abort, redirect
from datetime import datetime
import requests
import re
from urllib.parse import urlunparse, urlparse

app = Flask(__name__)

CARDAPIOWEB_BASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_TOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT = '14104'
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

# Dicionário em memória dos pedidos pendentes
PEDIDOS_PENDENTES = {}

def agora():
    return datetime.utcnow().isoformat() + "Z"

def remove_nulls(obj):
    if isinstance(obj, dict):
        return {k: remove_nulls(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj]
    else:
        return obj

def verify_token(request):
    token1 = request.headers.get("XApiKey")
    token2 = request.headers.get("Authorization")
    if token1 == CONSUMER_API_TOKEN or (token2 and token2.split()[-1] == CONSUMER_API_TOKEN):
        return True
    return False

@app.before_request
def canonicalize_url_remove_double_slash():
    """
    Redireciona qualquer requisição com barras duplas (//) no path para
    a versão canônica (com apenas barras simples), usando HTTP 308.
    """
    path = request.path
    if '//' in path:
        canonical_path = re.sub(r'/+', '/', path)
        parsed_url = urlparse(request.url)
        canonical_url = urlunparse(parsed_url._replace(path=canonical_path))
        print(f"[INFO] Redirecionando barra dupla: {request.url} --> {canonical_url}")
        return redirect(canonical_url, code=308)

def transform_order_data(order):
    """
    Formata o pedido para o padrão esperado pelo Consumer.
    """
    customer = order.get("customer") or {}
    phone = customer.get("phone", "")
    if isinstance(phone, str):
        customer["phone"] = {"number": phone}
    elif isinstance(phone, dict):
        customer["phone"] = phone
    else:
        customer["phone"] = {"number": str(phone)}

    def fix_option(opt):
        return {
            "optionId": opt.get("option_id", opt.get("optionId")),
            "externalCode": opt.get("external_code", opt.get("externalCode")),
            "name": opt.get("name", ""),
            "optionGroupId": opt.get("option_group_id", opt.get("optionGroupId")),
            "optionGroupName": opt.get("option_group_name", opt.get("optionGroupName")),
            "quantity": int(opt.get("quantity", 1)),
            "unitPrice": float(opt.get("unit_price", opt.get("unitPrice", 0))),
            "optionGroupTotalSelectedOptions": opt.get("option_group_total_selected_options", opt.get("optionGroupTotalSelectedOptions")),
        }

    def fix_item(item):
        return {
            "id": str(item.get("item_id", item.get("id", ""))),
            "externalCode": item.get("external_code", item.get("externalCode")),
            "name": item.get("name", ""),
            "quantity": int(item.get("quantity", 1)),
            "unitPrice": float(item.get("unit_price", item.get("unitPrice", 0))),
            "totalPrice": float(item.get("total_price", item.get("totalPrice", 0))),
            "observations": item.get("observation", item.get("observations", "")),
            "options": [fix_option(opt) for opt in item.get("options", [])]
        }

    items = [fix_item(i) for i in order.get("items", [])]
    delivery = order.get("delivery", {})
    address = delivery.get("deliveryAddress") or order.get("delivery_address") or {}
    delivery_fixed = {
        "deliveredBy": delivery.get("deliveredBy", order.get("delivered_by", "")),
        "deliveryDateTime": delivery.get("deliveryDateTime", order.get("created_at", agora())),
        "mode": delivery.get("mode", order.get("delivered_by", "")),
        "pickupCode": delivery.get("pickupCode", None),
        "deliveryAddress": {
            "country": address.get("country", "Brasil"),
            "state": address.get("state", ""),
            "city": address.get("city", ""),
            "postalCode": address.get("postalCode", address.get("postal_code", "")),
            "streetName": address.get("streetName", address.get("street", "")),
            "streetNumber": address.get("streetNumber", address.get("number", "")),
            "neighborhood": address.get("neighborhood", ""),
            "complement": address.get("complement", ""),
            "reference": address.get("reference", "")
        }
    }
    order_id = str(order.get("id"))
    base = {
        "orderId": order_id,
        "id": order_id,
        "displayId": str(order.get("display_id", "")),
        "orderType": order.get("order_type", "").upper(),
        "salesChannel": order.get("sales_channel", "").upper(),
        "orderTiming": order.get("order_timing", "").upper(),
        "createdAt": order.get("created_at", agora()),
        "customer": customer,
        "delivery": delivery_fixed,
        "items": items,
        "merchant": {
            "id": str(order.get("merchant_id", "")),
            "name": "Seu Restaurante"
        },
        "total": order.get("total", 0.0),
        "payments": order.get("payments", []),
        "status": "NEW",
        "fullCode": "PLACED",
        "code": "PLC"
    }
    return remove_nulls(base)

@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_orders():
    event = request.get_json()
    print(f"[WEBHOOK] Recebido: {event}")
    order_id = event.get("id") or event.get("order_id")
    if not order_id:
        print("[ERRO] Payload sem id/order_id:", event)
        return jsonify({"error": "Payload inesperado, sem id/order_id", "raw": event}), 400

    # Detecta CardapioWeb e busca detalhe via API, se necessário
    if "order_id" in event and len(event.keys()) <= 6:
        url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
        headers = {"X-API-KEY": CARDAPIOWEB_TOKEN, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT}
        print(f"[WEBHOOK] Solicitando detalhes do pedido ao CardapioWeb {url} ...")
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            print(f"[ERRO] Falha ao buscar detalhes do pedido {order_id} - StatusCode: {resp.status_code}")
            return jsonify({"error": "Falha ao obter detalhes"}), 500
        order = resp.json()
        print(f"[WEBHOOK] Detalhes recebidos do CardapioWeb: {order}")
    else:
        order = event

    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[str(order_id)] = pedido_transformado
    print(f"[ARMAZENADO] Pedido {order_id} armazenado em PEDIDOS_PENDENTES - Chaves atuais: {list(PEDIDOS_PENDENTES.keys())}")

    return jsonify({"success": True})

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    if not verify_token(request):
        print(f"[UNAUTHORIZED] Polling sem token correto.")
        return abort(401)
    # Responde SOMENTE cabeçalho dos pedidos, formato exigido pelo Consumer
    pedidos = []
    for p in PEDIDOS_PENDENTES.values():
        pedidos.append({
            "id": str(p.get("id")),
            "orderId": str(p.get("orderId", p.get("id"))),
            "createdAt": p.get("createdAt", agora()),
            "fullCode": p.get("fullCode", "PLACED"),
            "code": p.get("code", "PLC"),
        })
    print(f"[POLLING] Retornando {len(pedidos)} pedidos - {pedidos}")
    return jsonify({
        "items": pedidos,
        "statusCode": 0,
        "reasonPhrase": None
    })

@app.route('/meu-endpoint')
def meu_endpoint():
    return "ok"
