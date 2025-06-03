from flask import Flask, request, jsonify, abort, redirect
from datetime import datetime
import requests

app = Flask(__name__)

CARDAPIOWEB_BASE = 'https://integracao.cardapioweb.com/api/partner/v1'
CARDAPIOWEB_TOKEN = 'avsj9dEaxd5YdYBW1bYjEycETsp87owQYu6Eh2J5'
CARDAPIOWEB_MERCHANT = '14104'
CONSUMER_API_TOKEN = 'pk_live_zT3r7Y!a9b#2DfLkW8QzM0XeP4nGpVt-7uC@HjLsEw9Rx1YvKmZBdNcTfUqAy'

PEDIDOS_PENDENTES = {}

def agora():
    return datetime.utcnow().isoformat()

def remove_nulls(obj):
    if isinstance(obj, dict):
        return {k: remove_nulls(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj]
    else:
        return obj

def transform_order_data(order):
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
        "orderId": order_id,              # campo essencial para o Consumer!
        "id": order_id,                   # opcional, para compatibilidade
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
    return base

def verify_token(request):
    token1 = request.headers.get("XApiKey")
    token2 = request.headers.get("Authorization")
    if token1 == CONSUMER_API_TOKEN or (token2 and token2.split()[-1] == CONSUMER_API_TOKEN):
        return True
    return False

@app.route('/webhook/orders', methods=['POST'])
@app.route('/webhook/cardapioweb', methods=['POST'])
def webhook_orders():
    event = request.get_json()
    print("DEBUG: Recebido no webhook:", event)
    order_id = event.get("id") or event.get("order_id")
    if not order_id:
        return jsonify({"error": "Payload inesperado, sem id/order_id", "raw": event}), 400
    if "order_id" in event and len(event.keys()) <= 6:
        url = f"{CARDAPIOWEB_BASE}/orders/{order_id}"
        headers = {"X-API-KEY": CARDAPIOWEB_TOKEN, "Content-Type": "application/json"}
        params = {"merchant_id": CARDAPIOWEB_MERCHANT}
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            print("Erro ao buscar detalhes para o pedido:", order_id, "Status:", resp.status_code)
            return jsonify({"error": "Falha ao obter detalhes"}), 500
        order = resp.json()
    else:
        order = event
    pedido_transformado = transform_order_data(order)
    PEDIDOS_PENDENTES[str(order_id)] = pedido_transformado
    print(f"Pedido {order_id} capturado via webhook e armazenado.")
    return jsonify({"success": True})

@app.route('/api/parceiro/polling', methods=['GET'])
def polling():
    if not verify_token(request):
        return abort(401)
    pedidos = [remove_nulls(p) for p in PEDIDOS_PENDENTES.values()]
    for pedido in pedidos:
        pedido["status"] = "NEW"
        pedido["fullCode"] = "PLACED"
        pedido["code"] = "PLC"
    print(f"Polling: {len(pedidos)} pedidos pendentes")
    return jsonify({
        "items": pedidos
    })

@app.route('/api/parceiro/order/<path:anyid>', methods=['GET', 'POST'], strict_slashes=False)
def orderid_literal_fallback(anyid):
    anyid_norm = anyid.lstrip('/')
    pedido = PEDIDOS_PENDENTES.get(anyid_norm)
    if pedido:
        pedido_clean = remove_nulls(pedido)
        if request.method == 'POST':
            PEDIDOS_PENDENTES.pop(anyid_norm)
            print(f"Pedido {anyid_norm} removido após POST (integrado)")
        return jsonify(pedido_clean)
    print(f"Pedido {anyid_norm} não encontrado no dicionário PEDIDOS_PENDENTES.")
    return jsonify({"error": "Pedido não encontrado."}), 404

@app.route('/api/parceiro/order//<anyid>', methods=['GET', 'POST'], strict_slashes=False)
def orderid_fallback_double_bar(anyid):
    anyid_norm = anyid.lstrip('/')
    pedido = PEDIDOS_PENDENTES.get(anyid_norm)
    if pedido:
        pedido_clean = remove_nulls(pedido)
        if request.method == 'POST':
            PEDIDOS_PENDENTES.pop(anyid_norm)
            print(f"Pedido {anyid_norm} removido após POST (integrado)")
        return jsonify(pedido_clean)
    print(f"Pedido {anyid_norm} não encontrado na barra dupla.")
    return jsonify({"error": "Pedido não encontrado (barra dupla)."}), 404

#versão de 3 dias atras
